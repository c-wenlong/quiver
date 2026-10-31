import Foundation
import Testing
@testable import QuiverCore

/// The Tasks page (`GET /v1/app/tasks`): the wire reply's decode, the
/// row helpers, the list's filters and grouping, the detail's state
/// stepper, and the link/unlink routes. Mirrors the iPhone app's
/// TasksTests, which reads the same store through `GET /v1/tasks`.
@Suite struct TasksDecodingTests {
    /// `GET /v1/app/tasks` as the page's `tasks.py` joins it: the
    /// store over the sources' open items, `session_rows`/`launch_rows`
    /// filled, a failed repo and a blocked Linear.
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
                "draft": false, "url": "https://github.com/o/r/pull/9"},
         "session_rows": [
           {"key": "claude@/w:abc", "harness": "claude",
            "title": "fix the thing", "status": "active",
            "last_active_at": 1767323000.0}],
         "launch_rows": [
           {"id": "l-1", "state": "pending", "harness": "claude",
            "note": "Creating worktree quiver/issue-5…"}]},
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
         "last_active_at": 1767321000.0},
        {"id": "github:issue:o/r:7", "source": "github", "kind": "issue",
         "repo": "o/r", "ref": "7", "title": "A stale link",
         "url": "https://github.com/o/r/issues/7",
         "state": "in_progress", "sessions": ["codex:gone"],
         "launches": [], "agent_running": false, "linked": true,
         "last_active_at": 1767320000.0,
         "session_rows": [{"key": "codex:gone", "gone": true}]}
      ],
      "repos": [{"name": "o/s", "status": "upstream-error",
                 "error": "GitHub is down"},
                {"repo": "o/t", "status": "upstream-error",
                 "error": "timed out"}],
      "machine": "kai-macbook",
      "machine_id": "m-1",
      "github": {"status": "ok", "login": "octocat", "error": null},
      "linear": {"status": "auth-required", "login": null,
                 "error": "linear.key_ref is unset"},
      "env_blocked": null,
      "fetched_at": 1767323045.0
    }
    """

    @Test func decodesTheListReply() throws {
        let body = try HubWire.decoder.decode(HubTasksResponse.self, from: Data(Self.listJSON.utf8))
        #expect(body.tasks.count == 4)
        #expect(body.machine == "kai-macbook")
        #expect(body.machineId == "m-1")
        #expect(body.envBlocked == nil)
        #expect(body.github?.status == "ok")
        #expect(body.linear?.status == "auth-required")
        #expect(body.linear?.error == "linear.key_ref is unset")
        #expect(body.fetchedAt == 1767323045.0)

        let issue = body.tasks[0]
        #expect(issue.id == "github:issue:o/r:5")
        #expect(issue.status == "Agent running")
        #expect(issue.stateLabel == "Agent running")
        #expect(issue.scope == "o/r")
        #expect(issue.refLabel == "#5")
        #expect(issue.number == 5)
        #expect(issue.state == "in_progress")
        #expect(issue.sessions == ["claude@/w:abc"])
        #expect(issue.launches == ["l-1"])
        #expect(issue.agentRunning == true)
        #expect(issue.linked == true)
        #expect(issue.pr?.number == 9)
        #expect(issue.pr?.stateLabel == "Open")
        #expect(!issue.isDone)
        #expect(issue.updatedDate != nil)

        // The page's joins decode beside the ref fields.
        let session = try #require(issue.sessionRows?.first)
        #expect(session.key == "claude@/w:abc")
        #expect(session.status == "active")
        #expect(session.gone == nil)
        let launch = try #require(issue.launchRows?.first)
        #expect(launch.id == "l-1")
        #expect(launch.state == "pending")
        #expect(launch.note == "Creating worktree quiver/issue-5…")

        // A linked key the catalog dropped stays listed, marked gone.
        let stale = try #require(body.tasks[3].sessionRows?.first)
        #expect(stale.gone == true)

        let pull = body.tasks[1]
        #expect(pull.kind == "pull")
        #expect(pull.pr?.draft == true)
        #expect(TaskFilter.prOpen.includes(pull))

        let linear = body.tasks[2]
        #expect(linear.scope == "QUI")
        #expect(linear.refLabel == "QUI-123")
        #expect(linear.repo == nil)
        #expect(linear.number == nil)

        let failed = body.repos ?? []
        #expect(failed.count == 2)
        #expect(failed[0].name == "o/s")
        #expect(failed[0].error == "GitHub is down")
        // A `repo`-keyed failure (the pulls route's wording) reads too.
        #expect(failed[1].name == "o/t")
    }

    @Test func refBodyIsWhatTheLinkRouteTakes() throws {
        let body = try HubWire.decoder.decode(HubTasksResponse.self, from: Data(Self.listJSON.utf8))
        let issue = body.tasks[0].refBody
        #expect(issue["source"] as? String == "github")
        #expect(issue["kind"] as? String == "issue")
        #expect(issue["repo"] as? String == "o/r")
        #expect(issue["ref"] as? String == "5")
        #expect(issue["title"] as? String == "Fix the thing")
        #expect(issue["url"] as? String == "https://github.com/o/r/issues/5")
        #expect(issue["project"] == nil)
        let linear = body.tasks[2].refBody
        #expect(linear["project"] as? String == "QUI")
        #expect(linear["repo"] == nil)
    }
}

@Suite struct TaskRowTests {
    private func row(_ id: String, source: String? = "github",
                     kind: String? = "issue", ref: String? = "5",
                     repo: String? = "o/r", project: String? = nil,
                     title: String? = "t", url: String? = "u",
                     state: String? = "open", running: Bool? = false,
                     pr: HubTaskPr? = nil) -> HubTaskRow {
        HubTaskRow(id: id, source: source, kind: kind, ref: ref, title: title,
                   url: url, repo: repo, project: project, state: state,
                   sessions: nil, launches: nil, agentRunning: running,
                   linked: nil, lastActiveAt: nil, createdAt: nil,
                   updatedAt: nil, pr: pr)
    }

    @Test func filtersPickByStateAndAgent() {
        let running = row("a", state: "in_progress", running: true)
        let prOpen = row("b", state: "pr_open")
        let pull = row("c", kind: "pull", state: "open",
                       pr: HubTaskPr(number: 9, state: "open"))
        let merged = row("d", state: "merged")
        let open = row("e")

        #expect(TaskFilter.running.includes(running))
        #expect(!TaskFilter.running.includes(open))
        // A row whose flag never arrived doesn't count as running.
        #expect(!TaskFilter.running.includes(row("f", running: nil)))

        #expect(TaskFilter.prOpen.includes(prOpen))
        // A pull task with an open PR is its own PR-open row.
        #expect(TaskFilter.prOpen.includes(pull))
        // A pull task counts until it lands even when its pr block is
        // missing, as the web and the phone count it.
        #expect(TaskFilter.prOpen.includes(row("g", kind: "pull", state: "open")))
        #expect(!TaskFilter.prOpen.includes(row("g2", kind: "pull", state: "merged")))
        #expect(!TaskFilter.prOpen.includes(open))
        #expect(!TaskFilter.prOpen.includes(merged))

        #expect(TaskFilter.done.includes(merged))
        #expect(TaskFilter.done.includes(row("h", state: "closed")))
        #expect(!TaskFilter.done.includes(open))
        #expect(TaskFilter.all.includes(open))
    }

    @Test func sectionsGroupByScopeInReplyOrder() {
        let tasks = [
            row("b", repo: "o/s"),
            row("a", repo: "o/r"),
            row("c", repo: "o/r"),
            row("d", source: "linear", ref: "QUI-1", repo: nil, project: "QUI"),
            row("e", source: "notion", kind: "page", ref: "x", repo: nil),
        ]
        let sections = TaskGrouping.sections(of: tasks, filter: .all)
        #expect(sections.map(\.name) == ["o/s", "o/r", "QUI", "Notion"])
        #expect(sections[1].rows?.map(\.id) == ["a", "c"])
        // Only a GitHub row's section can link out to the repo.
        #expect(sections[0].repo == "o/s")
        #expect(sections[2].repo == nil)
        #expect(sections[3].repo == nil)
        #expect(TaskGrouping.count(tasks, filter: .all) == 5)

        // A scope whose rows all filter out drops its section.
        let none = TaskGrouping.sections(of: [row("a", running: false)], filter: .running)
        #expect(none.isEmpty)
    }

    /// GitHub is spelled GitHub, and a row naming no source at all is
    /// the only one under "Other".
    @Test func sourcesAreSpelledAsThePagesSpellThem() {
        let tasks = [
            row("a", repo: nil),
            row("b", source: nil, kind: "page", ref: "x", repo: nil),
        ]
        #expect(TaskGrouping.sections(of: tasks, filter: .all).map(\.name) == ["GitHub", "Other"])
        #expect(TaskGrouping.rail(of: tasks, filter: .all).map(\.title) == ["GitHub", "Other"])
    }

    @Test func railGroupsScopesBySourceAndCountsUnderTheFilter() {
        let tasks = [
            row("a", repo: "o/s", running: true),
            row("b", source: "linear", ref: "QUI-1", repo: nil, project: "QUI"),
            row("c", repo: "p/x"),
            row("d", repo: "o/r"),
            row("e", repo: "o/s"),
            row("f", source: "notion", kind: "page", ref: "x", repo: nil),
        ]
        let failed = [HubTaskRepoFailure(name: "p/a", error: "gh timed out")]
        let rail = TaskGrouping.rail(of: tasks, failed: failed, filter: .running)
        // Owners by name, then the other sources by name; rows by name,
        // so the rail holds still while the list reorders by activity.
        #expect(rail.map(\.title) == ["o", "p", "Linear", "Notion"])
        #expect(rail[0].scopes.map(\.label) == ["r", "s"])
        #expect(rail[0].scopes.map(\.name) == ["o/r", "o/s"])
        // A failed repo with no rows still gets a row, quiet.
        #expect(rail[1].scopes.map(\.name) == ["p/a", "p/x"])
        #expect(rail[1].scopes.map(\.count) == [0, 0])
        #expect(rail[2].scopes.map(\.label) == ["QUI"])
        #expect(rail[3].scopes.map(\.name) == ["Notion"])
        // Every scope keeps its row; the count is what the chip leaves,
        // so a scope with none still lists (the rail folds it as quiet).
        #expect(rail[0].scopes.map(\.count) == [0, 1])
        #expect(rail[0].scopes.map(\.running) == [false, true])
        #expect(TaskGrouping.rail(of: tasks, filter: .all)[0].scopes.map(\.count) == [1, 2])

        // A rail pick narrows the sections and the chip counts alike.
        let picked = TaskGrouping.sections(of: tasks, filter: .all, scope: "o/s")
        #expect(picked.map(\.name) == ["o/s"])
        #expect(picked[0].rows?.map(\.id) == ["a", "e"])
        #expect(TaskGrouping.count(tasks, filter: .all, scope: "o/s") == 2)
        #expect(TaskGrouping.count(tasks, filter: .running, scope: "QUI") == 0)
    }

    @Test func aFailedRepoJoinsItsSectionOrKeepsOneForItsError() {
        let tasks = [row("a", repo: "o/r"), row("b", repo: "o/s")]
        let failed = [HubTaskRepoFailure(name: "o/r", error: "gh timed out"),
                      HubTaskRepoFailure(name: "o/t", status: "upstream-error")]
        let sections = TaskGrouping.sections(of: tasks, failed: failed, filter: .all)
        // o/r keeps one header with its rows and the reason on it.
        #expect(sections.map(\.name) == ["o/r", "o/s", "o/t"])
        #expect(sections[0].error == "gh timed out")
        #expect(sections[0].rows?.map(\.id) == ["a"])
        #expect(sections[1].error == nil)
        #expect(sections[2].rows?.isEmpty == true)
        #expect(sections[2].error == "upstream-error")
        #expect(sections[2].repo == "o/t")
        // A rail pick leaves out every other repo's failure.
        let picked = TaskGrouping.sections(of: tasks, failed: failed, filter: .all, scope: "o/s")
        #expect(picked.map(\.name) == ["o/s"])
    }

    @Test func sourceLineAndLabels() {
        #expect(row("a", kind: "issue").sourceLine == "GitHub issue · o/r")
        #expect(row("a", kind: "pull").sourceLine == "GitHub pull request · o/r")
        #expect(row("a", source: "linear", repo: nil, project: "QUI").sourceLine == "Linear · QUI")
        #expect(row("a", source: "notion", kind: "page", repo: nil, project: "Roadmap").sourceLine
            == "Notion · Roadmap")
        #expect(row("a", source: "notion", kind: "page", repo: nil).sourceLine == "Notion")
        #expect(row("a").openLabel == "Open on GitHub")
        #expect(row("a", source: "linear", repo: nil, project: "QUI").openLabel == "Open in Linear")
        #expect(row("a", source: "notion", kind: "page", repo: nil).openLabel == "Open in Notion")
        // The ladder is never the text (#875): an issue whose agent has
        // finished reads Open, a running one Agent running.
        #expect(row("a", state: "in_progress").stateLabel == "Open")
        #expect(row("a", state: "in_progress", running: true).stateLabel == "Agent running")
        #expect(row("a", state: "pr_open").stateLabel == "PR open")
        #expect(row("a", state: "weird").stateLabel == "Open")
        // The hub's own status wins, in the source's words; without one,
        // started work on another source still reads Started.
        var linear = row("a", source: "linear", repo: nil, project: "QUI", state: "in_progress")
        #expect(linear.stateLabel == "Started")
        linear.status = "In Review"
        #expect(linear.stateLabel == "In Review")
        // A bare ref stays itself; GitHub's reads #5.
        #expect(row("a", source: "linear", ref: "QUI-1", repo: nil, project: "QUI").refLabel == "QUI-1")
        #expect(row("a").refLabel == "#5")
        #expect(HubTaskPr(number: 1, state: "merged").stateLabel == "Merged")
        #expect(HubTaskPr(number: 1, state: "closed").stateLabel == "Closed")
        #expect(HubTaskPr(number: 1, state: "open").stateLabel == "Open")
    }

    @Test func launchRoutesExistOnlyForAGithubIssueOrPull() {
        #expect(row("a", kind: "issue").canLaunch)
        #expect(row("a", kind: "pull").canLaunch)
        // No repo, no route: the launch needs owner/repo.
        #expect(!row("a", repo: nil).canLaunch)
        #expect(!row("a", repo: "").canLaunch)
        #expect(!row("a", source: "linear", repo: nil, project: "QUI").canLaunch)
        #expect(!row("a", source: "notion", kind: "page", repo: nil).canLaunch)
    }
}

@Suite struct TaskStepperTests {
    private func row(source: String? = "github", kind: String? = "issue",
                     state: String? = "open") -> HubTaskRow {
        HubTaskRow(id: "t", source: source, kind: kind, ref: "5",
                   title: "t", url: "u", repo: "o/r", project: nil,
                   state: state, sessions: nil, launches: nil,
                   agentRunning: false, linked: nil, lastActiveAt: nil,
                   createdAt: nil, updatedAt: nil, pr: nil)
    }

    private func step(_ title: String, _ reached: Bool, _ current: Bool) -> TaskStepper.Step {
        TaskStepper.Step(title: title, reached: reached, current: current)
    }

    @Test func issueLadderReachesUpToTheState() {
        #expect(TaskStepper.steps(for: row(state: "pr_open"))
            == [step("Open", true, false), step("Agent", true, false),
                step("PR open", true, true), step("Merged", false, false)])
        #expect(TaskStepper.steps(for: row(state: "merged")).last
            == step("Merged", true, true))
        #expect(TaskStepper.steps(for: row()).first == step("Open", true, true))
    }

    @Test func pullLadderSkipsThePrRung() {
        let steps = TaskStepper.steps(for: row(kind: "pull", state: "in_progress"))
        #expect(steps.map(\.title) == ["Open", "Agent", "Merged"])
        #expect(steps[1] == step("Agent", true, true))
    }

    @Test func nonGithubLaddersSkipThePrRung() {
        // Linear/Notion issues and cards can never produce a PR, so the
        // rung must not exist — same rule the web page's `rungs()` uses.
        let linear = TaskStepper.steps(for: row(source: "linear", state: "in_progress"))
        #expect(linear.map(\.title) == ["Open", "Started", "Done"])
        #expect(linear[1] == step("Started", true, true))
        let notion = TaskStepper.steps(
            for: row(source: "notion", kind: "card", state: "in_progress"))
        #expect(notion.map(\.title) == ["Open", "Started", "Done"])
        // A closed non-GitHub task still lands on the Closed rung.
        let closed = TaskStepper.steps(for: row(source: "linear", state: "closed"))
        #expect(closed.last == step("Closed", true, true))
        #expect(closed.map(\.reached) == [true, false, true])
    }

    @Test func closedReplacesTheTerminalRung() {
        let steps = TaskStepper.steps(for: row(state: "closed"))
        #expect(steps.last == step("Closed", true, true))
        // How far a hand-closed task got isn't inferable: only its
        // ends light up.
        #expect(steps.map(\.reached) == [true, false, false, true])
        let pull = TaskStepper.steps(for: row(kind: "pull", state: "closed"))
        #expect(pull.last == step("Closed", true, true))
    }

    @Test func unknownStatesReadAsOpen() {
        #expect(TaskStepper.steps(for: row(state: nil)).first == step("Open", true, true))
        #expect(TaskStepper.steps(for: row(state: "triage")).first == step("Open", true, true))
    }
}

/// The new-session sheet's pickers, ported from the iPhone app's
/// `NewSessionOptions` tests: which machines can launch, the folders
/// their sessions ran in, and the harness a folder last used.
@Suite struct NewSessionOptionsTests {
    private func session(_ key: String, machineId: String?, cwd: String?,
                         harness: String? = nil, agent: String? = nil,
                         lastActiveAt: Double? = nil) -> HubSessionRow {
        HubSessionRow(key: key, harness: harness, agent: agent, cwd: cwd,
                      machineId: machineId, lastActiveAt: lastActiveAt)
    }

    @Test func foldersAreTheMachinesRecentOnes() {
        let rows = [
            session("a", machineId: "m1", cwd: "/p/old", lastActiveAt: 10),
            session("b", machineId: "m2", cwd: "/p/other", lastActiveAt: 30),
            session("c", machineId: "m1", cwd: "/p/new", lastActiveAt: 20),
            session("d", machineId: "m1", cwd: "/p/old", lastActiveAt: 40),
            session("e", machineId: "m1", cwd: "relative", lastActiveAt: 50),
            session("f", machineId: nil, cwd: "/p/local", lastActiveAt: 60),
        ]
        // Newest first, each once, absolute paths on this machine only —
        // a satellite's folders don't list under the hub's.
        #expect(NewSessionOptions.folders(on: "m1", sessions: rows) == ["/p/old", "/p/new"])
        #expect(NewSessionOptions.folders(on: "m2", sessions: rows) == ["/p/other"])
        #expect(NewSessionOptions.folders(on: "m3", sessions: rows).isEmpty)
    }

    /// A hub that answers `launchable: []` is current: updating swe would
    /// change nothing, so the sheet says to install or turn one on. Only
    /// a row without the field, or no rows, means swe is too old.
    @Test func theNoMachineMessageFollowsWhatTheHubReported() {
        let hub = HubMachine(id: "m1", harnesses: ["claude"], launchable: [])
        #expect(NewSessionOptions.noMachineMessage([hub])
                == "No harness can start on the hub: install one there, or turn it on in Settings › Providers.")
        let satellite = HubMachine(id: "s1", harnesses: [], launchable: [])
        #expect(NewSessionOptions.noMachineMessage([hub, satellite]).hasPrefix("No harness can start on any machine"))
        let old = HubMachine(id: "m1", harnesses: [])
        #expect(NewSessionOptions.noMachineMessage([old]).hasSuffix("Update swe on the hub."))
        #expect(NewSessionOptions.noMachineMessage([]).hasSuffix("Update swe on the hub."))
    }

    @Test func harnessesPreferLaunchable() {
        let m = HubMachine(id: "m1", harnesses: ["claude"],
                           launchable: ["qwen-code", "claude", "kilo"])
        #expect(NewSessionOptions.harnesses(on: m) == ["qwen-code", "claude", "kilo"])
        // An older hub row without `launchable` offers what it detected.
        let old = HubMachine(id: "m2", harnesses: ["claude", "gemini"])
        #expect(NewSessionOptions.harnesses(on: old) == ["claude", "gemini"])
        #expect(NewSessionOptions.harnesses(on: HubMachine(id: "m3")).isEmpty)
    }

    @Test func machinesSortOnlineFirstAndSkipTheUnlaunchable() {
        let ms = [
            HubMachine(id: "b", name: "b.local", online: false, launchable: ["claude"]),
            HubMachine(id: "z", online: true, launchable: ["claude"]),
            HubMachine(id: "o", online: true, launchable: ["claude"]),
            HubMachine(id: "nope", online: true),
        ]
        // Online first, names within; nothing-launchable is left out.
        #expect(NewSessionOptions.machines(ms).map(\.id) == ["o", "z", "b"])
    }

    @Test func folderFilterMatchesEveryWord() {
        let folders = ["/Users/k/Projects/quiver-render", "/Users/k/Projects/quiver", "/tmp"]
        #expect(NewSessionOptions.matching(folders, query: "QUIVER rend") == ["/Users/k/Projects/quiver-render"])
        #expect(NewSessionOptions.matching(folders, query: "  ") == folders)
        #expect(NewSessionOptions.matching(folders, query: "nothing").isEmpty)
    }

    @Test func harnessNamesComeFromTheMachineThenSessions() {
        let m = HubMachine(id: "m", harnessNames: ["devin": "Devin"])
        let rows = [HubSessionRow(key: "k", harness: "kilo", agent: "Kilo CLI")]
        #expect(NewSessionOptions.name("devin", on: m, sessions: rows) == "Devin")
        #expect(NewSessionOptions.name("kilo", on: m, sessions: rows) == "Kilo CLI")
        // Unknown anywhere: the id, first letter up.
        #expect(NewSessionOptions.name("pi", on: nil, sessions: []) == "Pi")
    }

    @Test func aFoldersLastHarnessWinsWhenOffered() {
        let rows = [
            session("a", machineId: "m1", cwd: "/p", harness: "claude", lastActiveAt: 10),
            session("b", machineId: "m1", cwd: "/p", harness: "codex", lastActiveAt: 20),
        ]
        #expect(NewSessionOptions.defaultHarness(folder: "/p", machineId: "m1",
                                                 offered: ["claude", "codex"], sessions: rows) == "codex")
        // The folder's last harness off the offer falls back to the first.
        #expect(NewSessionOptions.defaultHarness(folder: "/p", machineId: "m1",
                                                 offered: ["claude"], sessions: rows) == "claude")
        #expect(NewSessionOptions.defaultHarness(folder: "/elsewhere", machineId: "m1",
                                                 offered: ["claude"], sessions: rows) == "claude")
        #expect(NewSessionOptions.defaultHarness(folder: "/p", machineId: "m1",
                                                 offered: [], sessions: rows) == nil)
    }

    /// The launch Model row (#914): shown only where the launch fields
    /// exist, enabled only where the picked harness is listed, prefilled
    /// from the machine's ledger, and sent only as a non-blank token.
    @Test func launchOptionsReadTheMachinesModelFields() {
        let machine = HubMachine(id: "m", launchable: ["claude", "codex"],
                                 modelAtLaunch: ["claude"],
                                 lastModels: ["claude": "claude-sonnet-4.5"])
        #expect(LaunchOptions.showsModel(on: machine))
        #expect(LaunchOptions.takesModel("claude", on: machine))
        #expect(!LaunchOptions.takesModel("codex", on: machine))
        #expect(!LaunchOptions.takesModel(nil, on: machine))
        // An old hub sends only `last_models`: a one-item list.
        #expect(LaunchOptions.recentModels("claude", on: machine) == ["claude-sonnet-4.5"])
        #expect(LaunchOptions.recentModels("codex", on: machine) == [])
        #expect(LaunchOptions.recentModels(nil, on: machine) == [])

        // A row that reports the key but lists nothing shows the row
        // disabled; a row too old to report it shows nothing.
        let noneTake = HubMachine(id: "m2", modelAtLaunch: [])
        #expect(LaunchOptions.showsModel(on: noneTake))
        #expect(!LaunchOptions.takesModel("claude", on: noneTake))
        let old = HubMachine(id: "m3", launchable: ["claude"])
        #expect(!LaunchOptions.showsModel(on: old))
        #expect(!LaunchOptions.takesModel("claude", on: old))
        #expect(!LaunchOptions.showsModel(on: nil))

        // The body's `model`: trimmed, gone when blank or unsupported.
        #expect(LaunchOptions.model(" sonnet ", supported: true) == "sonnet")
        #expect(LaunchOptions.model("provider/x", supported: true) == "provider/x")
        #expect(LaunchOptions.model("  ", supported: true) == nil)
        #expect(LaunchOptions.model("sonnet", supported: false) == nil)
    }

    /// The Autonomy row reads the same gate: it shows only on a machine
    /// row reporting `yolo_at_launch`, ticks only for a listed harness,
    /// and the body carries `yolo` only when the switch is on.
    @Test func launchOptionsReadTheMachinesYoloFields() {
        let machine = HubMachine(id: "m", launchable: ["claude", "gemini"],
                                 yoloAtLaunch: ["claude"])
        #expect(LaunchOptions.showsYolo(on: machine))
        #expect(LaunchOptions.takesYolo("claude", on: machine))
        #expect(!LaunchOptions.takesYolo("gemini", on: machine))
        #expect(!LaunchOptions.takesYolo(nil, on: machine))

        // A row that reports the key but lists nothing shows the box
        // off; a row too old to report it shows nothing.
        let noneTake = HubMachine(id: "m2", yoloAtLaunch: [])
        #expect(LaunchOptions.showsYolo(on: noneTake))
        #expect(!LaunchOptions.takesYolo("claude", on: noneTake))
        let old = HubMachine(id: "m3", launchable: ["claude"])
        #expect(!LaunchOptions.showsYolo(on: old))
        #expect(!LaunchOptions.showsYolo(on: nil))

        // The body's `yolo`: true only when sent, absent otherwise.
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go",
                                   yolo: true)["yolo"] as? Bool == true)
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go")["yolo"] == nil)
    }

    /// `recent_models` wins over `last_models`; a task-harness row reads
    /// the same way, blanks and repeats dropped.
    @Test func recentModelsPreferTheListAndFallBackToTheLastModel() {
        let machine = HubMachine(id: "m", modelAtLaunch: ["claude"],
                                 lastModels: ["claude": "opus", "codex": "gpt-5"],
                                 recentModels: ["claude": ["opus", "sonnet", "haiku"]])
        #expect(LaunchOptions.recentModels("claude", on: machine) == ["opus", "sonnet", "haiku"])
        // A harness the map leaves out reads as none: the fallback is
        // only for a row with no `recent_models` at all.
        #expect(LaunchOptions.recentModels("codex", on: machine) == [])
        #expect(LaunchOptions.recentModels("claude", on: nil) == [])

        let row = HubTaskHarness(name: "claude", launchModel: true, lastModel: "opus",
                                 recentModels: ["opus", "", "opus", "sonnet"])
        #expect(LaunchOptions.recentModels(of: row) == ["opus", "sonnet"])
        let oldRow = HubTaskHarness(name: "claude", launchModel: true, lastModel: "opus")
        #expect(LaunchOptions.recentModels(of: oldRow) == ["opus"])
        let blankRow = HubTaskHarness(name: "codex", launchModel: true, lastModel: "")
        #expect(LaunchOptions.recentModels(of: blankRow) == [])
        #expect(LaunchOptions.recentModels(of: nil) == [])
    }

    /// The menu: default, recent newest first, Other — defaulting to
    /// the newest model, else "Harness default".
    @Test func modelMenuListsDefaultRecentThenOther() {
        #expect(ModelPick.options(["opus", "sonnet"])
                == [.harnessDefault, .recent("opus"), .recent("sonnet"), .other])
        #expect(ModelPick.options([]) == [.harnessDefault, .other])
        #expect(ModelPick.defaultChoice(["opus", "sonnet"]) == .recent("opus"))
        #expect(ModelPick.defaultChoice([]) == .harnessDefault)
        #expect(ModelChoice.harnessDefault.title == "Harness default")
        #expect(ModelChoice.other.title == "Other…")
        #expect(ModelChoice.recent("opus").title == "opus")

        let fresh = ModelPick(recent: ["opus"])
        #expect(fresh.choice == .recent("opus"))
        #expect(!fresh.picked)
    }

    /// What the body carries for each choice.
    @Test func modelMenuSendsTheChosenModelOnly() {
        var pick = ModelPick(recent: ["opus"])
        #expect(pick.model(supported: true) == "opus")
        #expect(pick.model(supported: false) == nil)
        pick.pick(.harnessDefault)
        #expect(pick.model(supported: true) == nil)
        pick.pick(.other)
        #expect(pick.model(supported: true) == nil)
        pick.otherText = "  "
        #expect(pick.model(supported: true) == nil)
        pick.otherText = " provider/x "
        #expect(pick.model(supported: true) == "provider/x")
        #expect(pick.model(supported: false) == nil)
        // Typed text left behind by a switch back to a recent model
        // never travels.
        pick.pick(.recent("opus"))
        #expect(pick.model(supported: true) == "opus")
    }

    /// The survival rules across harness and machine switches.
    @Test func modelMenuRefitsAcrossSwitches() {
        // Untouched: each switch re-defaults to the new newest model.
        var pick = ModelPick()
        pick.refit(["opus", "sonnet"])
        #expect(pick.choice == .recent("opus"))
        pick.refit(["gpt-5"])
        #expect(pick.choice == .recent("gpt-5"))
        pick.refit([])
        #expect(pick.choice == .harnessDefault)
        #expect(!pick.picked)

        // An explicit "Harness default" stays, even where models exist.
        pick.pick(.harnessDefault)
        pick.refit(["opus"])
        #expect(pick.choice == .harnessDefault)
        #expect(pick.picked)

        // Other keeps its typed text.
        pick.pick(.other)
        pick.otherText = "my-model"
        pick.refit(["gpt-5"])
        #expect(pick.choice == .other)
        #expect(pick.model(supported: true) == "my-model")

        // A picked recent model stays while the new list has it...
        pick.pick(.recent("sonnet"))
        pick.refit(["opus", "sonnet"])
        #expect(pick.choice == .recent("sonnet"))
        #expect(pick.picked)
        // ...and shows the new list's default where it doesn't, so
        // another harness's model never travels.
        pick.refit(["gpt-5", "o3"])
        #expect(pick.choice == .recent("gpt-5"))
        #expect(!pick.picked)
        #expect(pick.model(supported: true) == "gpt-5")
        pick.pick(.recent("o3"))
        pick.refit([])
        #expect(pick.choice == .harnessDefault)
        #expect(!pick.picked)
        pick.refit(["opus"])
        #expect(pick.choice == .recent("opus"))
    }

    /// A blank "Other…" is a pick too: it survives a switch, still blank,
    /// and still sends no model.
    @Test func modelMenuKeepsABlankOtherAcrossSwitches() {
        var pick = ModelPick(recent: ["opus"])
        pick.pick(.other)
        pick.refit(["gpt-5"])
        #expect(pick.choice == .other)
        #expect(pick.otherText == "")
        #expect(pick.picked)
        #expect(pick.model(supported: true) == nil)
        pick.refit([])
        #expect(pick.choice == .other)
    }

    /// Claude's picked model survives a trip through Codex: Codex shows
    /// and sends its own newest model, and Claude gets the pick back.
    @Test func modelMenuRestoresAPickAfterAHarnessThatLacksIt() {
        let claude = ["claude-sonnet", "claude-opus"]
        var pick = ModelPick(recent: claude)
        pick.pick(.recent("claude-opus"))

        pick.refit(["gpt-5", "o3"])
        #expect(pick.choice == .recent("gpt-5"))
        #expect(!pick.picked)
        #expect(pick.model(supported: true) == "gpt-5")
        #expect(pick.remembered == "claude-opus")

        // A second foreign harness in a row keeps the memory too.
        pick.refit([])
        #expect(pick.choice == .harnessDefault)
        #expect(pick.model(supported: true) == nil)

        pick.refit(claude)
        #expect(pick.choice == .recent("claude-opus"))
        #expect(pick.picked)
        #expect(pick.model(supported: true) == "claude-opus")
    }

    /// A harness that can't take a model at launch (Kilo) sends none,
    /// whatever its list, and the pick comes back after it.
    @Test func modelMenuRestoresAPickAfterAnUnsupportedHarness() {
        let claude = ["claude-sonnet", "claude-opus"]
        var pick = ModelPick(recent: claude)
        pick.pick(.recent("claude-opus"))

        pick.refit([])
        #expect(pick.model(supported: false) == nil)
        pick.refit(["kilo/model"])
        #expect(pick.model(supported: false) == nil)

        pick.refit(claude)
        #expect(pick.choice == .recent("claude-opus"))
        #expect(pick.model(supported: true) == "claude-opus")
    }

    /// Picking anything else from the menu replaces the remembered model.
    @Test func modelMenuForgetsAPickWhenTheUserPicksAgain() {
        let claude = ["claude-sonnet", "claude-opus"]

        // Another recent model, here Codex's shown default picked by hand.
        var pick = ModelPick(recent: claude)
        pick.pick(.recent("claude-opus"))
        pick.refit(["gpt-5"])
        pick.pick(.recent("gpt-5"))
        #expect(pick.remembered == "gpt-5")
        pick.refit(claude)
        #expect(pick.choice == .recent("claude-sonnet"))
        #expect(!pick.picked)

        // "Harness default", which then stays put.
        pick = ModelPick(recent: claude)
        pick.pick(.recent("claude-opus"))
        pick.pick(.harnessDefault)
        #expect(pick.remembered == nil)
        pick.refit(claude)
        #expect(pick.choice == .harnessDefault)

        // "Other…", which stays put the same way.
        pick = ModelPick(recent: claude)
        pick.pick(.recent("claude-opus"))
        pick.pick(.other)
        #expect(pick.remembered == nil)
        pick.refit(claude)
        #expect(pick.choice == .other)
    }
}

/// The hub calls the pages make: paths, queries and bodies, answered
/// by the stub. Serial through `HubStubbed` with the other hub tests.
extension HubStubbed {
@MainActor
@Suite struct TasksRouteTests {
    private func client() -> HubClient {
        HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!,
                  credential: "secret", session: HubStub.session())
    }

    /// A request's JSON body with nested objects, where
    /// `HubStub.body(_:)` flattens to strings.
    private func body(_ request: URLRequest) throws -> [String: Any] {
        try #require(JSONSerialization.jsonObject(with: HubStub.bodyData(request)) as? [String: Any])
    }

    @Test func appTasksReadsThePagePayload() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks"
                ? (200, Data(#"{"tasks": [], "machine": "m", "machine_id": "m1"}"#.utf8))
                : (404, Data())
        }
        let reply = try await client().appTasks(refresh: true)
        #expect(reply.machineId == "m1")
        let req = try #require(HubStub.requests.first)
        #expect(req.httpMethod == "GET")
        #expect(HubStub.query(req) == ["refresh": "1"])
    }

    @Test func taskHarnessesAskTheSameRoute() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks"
                ? (200, Data(#"{"harnesses": [{"name": "claude", "display_name": "Claude Code", "tracked": true, "launch_model": true, "launch_effort": true, "last_model": "claude-sonnet-4.5", "recent_models": ["claude-sonnet-4.5", "claude-opus-4.1"]}, {"name": "codex", "display_name": "Codex", "tracked": true, "launch_model": false, "launch_effort": false, "last_model": ""}], "machine": "kai"}"#.utf8))
                : (404, Data())
        }
        let (harnesses, machine) = try await client().taskHarnesses()
        #expect(harnesses.map(\.name) == ["claude", "codex"])
        #expect(harnesses.first?.displayName == "Claude Code")
        #expect(harnesses.first?.tracked == true)
        // The launch-option fields decode; an older hub leaves them nil.
        #expect(harnesses.first?.launchModel == true)
        #expect(harnesses.first?.launchEffort == true)
        #expect(harnesses.first?.lastModel == "claude-sonnet-4.5")
        #expect(harnesses.first?.recentModels == ["claude-sonnet-4.5", "claude-opus-4.1"])
        #expect(harnesses[1].launchModel == false)
        #expect(harnesses[1].recentModels == nil)
        #expect(machine == "kai")
        #expect(HubStub.requests.first.map(HubStub.query) == ["harnesses": "1"])
    }

    @Test func linkAndUnlinkPostTheTaskRef() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path.hasSuffix("/task") == true ? (200, Data(#"{"ok": true}"#.utf8)) : (404, Data())
        }
        let ref: [String: any Sendable] = ["source": "github", "kind": "issue",
                                         "ref": "5", "title": "t",
                                         "url": "u", "repo": "o/r"]
        try await client().linkTask("claude:a@m1", ref: ref)
        let post = try #require(HubStub.requests.first)
        #expect(post.httpMethod == "POST")
        // The session key's `:` and `@` percent-encode in the path.
        #expect(post.url?.absoluteString.contains("/v1/sessions/claude%3Aa%40m1/task") == true)
        let sent = try body(post)
        #expect((sent["task_ref"] as? [String: Any])?["repo"] as? String == "o/r")
        #expect((sent["task_ref"] as? [String: Any])?["ref"] as? String == "5")

        HubStub.requests = []
        try await client().unlinkTask("claude:a@m1", ref: ref)
        let del = try #require(HubStub.requests.first)
        #expect(del.httpMethod == "DELETE")
        #expect(del.url?.absoluteString.contains("/v1/sessions/claude%3Aa%40m1/task") == true)
    }
}

@MainActor
@Suite struct NewSessionRouteTests {
    private func client() -> HubClient {
        HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!,
                  credential: "secret", session: HubStub.session())
    }

    @Test func launchBodyCarriesTheHarnessCwdAndPrompt() {
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go") as? [String: String]
            == ["harness": "claude", "cwd": "/p", "prompt": "go"])
        // A satellite is named; the hub itself is the omitted default.
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go",
                                     machine: "m2")["machine"] as? String == "m2")
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go")["machine"] == nil)
        // A model pick travels only as a non-blank, trimmed token.
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go",
                                     model: " sonnet ")["model"] as? String == "sonnet")
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go",
                                     model: "  ")["model"] == nil)
    }

    @Test func startSessionPostsAndReadsTheLaunch() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/sessions" && req.httpMethod == "POST"
                ? (202, Data(#"{"launch_id": "l-9", "state": "pending"}"#.utf8))
                : (404, Data())
        }
        let launch = try await client().startSession(harness: "claude", cwd: "/p",
                                                     prompt: "fix it", machine: "m2",
                                                     model: "sonnet")
        #expect(launch.launchId == "l-9")
        #expect(launch.state == "pending")
        let sent = HubStub.body(try #require(HubStub.requests.first))
        #expect(sent == ["harness": "claude", "cwd": "/p", "prompt": "fix it",
                         "machine": "m2", "model": "sonnet"])
    }

    @Test func launchPollReadsWhereItStands() async throws {
        HubStub.handler = { req in
            req.url?.path == "/v1/launches/l-9"
                ? (200, Data(#"{"launch_id": "l-9", "state": "started", "key": "claude:s1"}"#.utf8))
                : (404, Data())
        }
        let status = try await client().launch("l-9")
        #expect(status.state == "started")
        #expect(status.key == "claude:s1")
    }

    @Test func openPullPostsTheHarnessAndPrompt() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/pulls/o/r/9/open"
                ? (202, Data(#"{"launch_id": "l-2", "state": "pending"}"#.utf8))
                : (404, Data())
        }
        _ = try await client().openPull(owner: "o", repo: "r", number: 9,
                                        harness: "codex", prompt: "review it",
                                        model: "gpt-5")
        let sent = HubStub.body(try #require(HubStub.requests.first))
        #expect(sent == ["harness": "codex", "prompt": "review it", "model": "gpt-5"])
        // A blank prompt is left out: the hub's built one wins.
        _ = try await client().openPull(owner: "o", repo: "r", number: 9, harness: "codex")
        #expect(HubStub.body(try #require(HubStub.requests.last)) == ["harness": "codex"])
    }

    @Test func foldersPassThePathAsAQuery() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/folders"
                ? (200, Data(#"{"path": "/Users/k", "parent": "/Users", "folders": [{"name": "quiver", "path": "/Users/k/quiver"}], "truncated": false}"#.utf8))
                : (404, Data())
        }
        let listing = try await client().folders(path: "/Users/k")
        #expect(listing.path == "/Users/k")
        #expect(listing.parent == "/Users")
        #expect(listing.folders.first?.name == "quiver")
        #expect(HubStub.requests.first.map(HubStub.query) == ["path": "/Users/k"])
        // The default is the hub user's home: no query.
        _ = try await client().folders()
        #expect(HubStub.requests.last.map(HubStub.query) == [:])
    }
}

/// `TasksModel` over a stubbed hub: the load's fills, a failed refresh
/// keeping the rows it had, and link/unlink writing the store.
@MainActor
@Suite struct TasksModelTests {
    /// Each test gets its own instance, so its folder goes when the test ends.
    private let scratch: TempDir
    private var dir: URL { scratch.url }
    init() throws { scratch = try TempDir() }

    /// The page payload the stub answers — `nonisolated` so the
    /// Sendable request handler can read it.
    nonisolated private static let listJSON = """
    {"tasks": [
       {"id": "github:issue:o/r:5", "source": "github", "kind": "issue",
        "repo": "o/r", "ref": "5", "title": "Fix the thing",
        "url": "https://github.com/o/r/issues/5",
        "state": "in_progress", "sessions": ["claude:a"],
        "launches": [], "agent_running": true, "linked": true,
        "last_active_at": 10.0}],
     "repos": [{"name": "o/s", "status": "upstream-error", "error": "down"}],
     "machine": "kai", "machine_id": "m-1",
     "github": {"status": "ok", "login": "u"},
     "linear": {"status": "auth-required", "error": "no key"},
     "notion": {"status": "bad-config", "error": "no status property"}}
    """

    private func hub() throws -> LocalHub {
        let store = HubDeviceStore(url: dir.appending(path: "\(UUID().uuidString)/app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        return LocalHub(locator: FakeLocator(address: "127.0.0.1:8477"), store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session())
        }
    }

    @Test func aLoadFillsThePageAndMarksStaleRows() async throws {
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks" ? (200, Data(Self.listJSON.utf8)) : (404, Data())
        }
        let model = TasksModel()
        await model.load(hub: try hub())
        #expect(model.tasks.map(\.id) == ["github:issue:o/r:5"])
        #expect(model.failedRepos.map(\.name) == ["o/s"])
        #expect(model.machine == "kai")
        #expect(model.machineId == "m-1")
        #expect(model.github?.login == "u")
        #expect(model.envBlocked == nil)
        #expect(model.linearBlocked == "no key")
        #expect(model.notionBlocked == "no status property")
        #expect(model.error == nil)
        // The failed repo's rows are last-known; a healthy one's aren't.
        #expect(model.stale(model.tasks[0]) == false)
    }

    @Test func aHubThatIsStillStartingIsRetriedUntilItAnswers() async throws {
        // The hub restarts on every app install: the first loads find it
        // down, and the page fills in once it answers, with no Refresh.
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks" ? (200, Data(Self.listJSON.utf8)) : (404, Data())
        }
        let store = HubDeviceStore(url: dir.appending(path: "\(UUID().uuidString)/app-device.json"))
        try store.save(HubCredential(deviceId: "d1", credential: "secret"), for: "127.0.0.1:8477")
        let locator = FakeLocator(address: nil)
        let hub = LocalHub(locator: locator, store: store) {
            HubClient(baseURL: $0, credential: $1, session: HubStub.session())
        }
        let model = TasksModel()
        let waits = WaitLog()
        await model.loadUntilHubAnswers(hub: hub) { wait in
            await waits.add(wait)
            // Up after the second wait.
            if await waits.count == 2 { locator.address = "127.0.0.1:8477" }
        }
        #expect(await waits.all == [.seconds(3), .seconds(6)])
        #expect(model.hubDown == false)
        #expect(model.error == nil)
        #expect(model.tasks.map(\.id) == ["github:issue:o/r:5"])
    }

    @Test func aRestartingHubIsRetriedWhetherOrNotTheAppHasAClient() {
        // No client yet: swe's status says the hub is down. A client the
        // app already holds: the connection itself fails. Both pass once
        // the hub is up; a misbound hub or a bad reply doesn't.
        #expect(TasksModel.hubNotAnswering(HubError(status: 0, code: "hub_down", message: "")))
        #expect(TasksModel.hubNotAnswering(HubError(status: 0, code: "network", message: "")))
        #expect(!TasksModel.hubNotAnswering(HubError(status: 0, code: "hub_blocked", message: "")))
        #expect(!TasksModel.hubNotAnswering(HubError(status: 500, code: "internal", message: "")))
    }

    @Test func aCancelledRetryStopsWithTheHubStillDown() async throws {
        let hub = LocalHub(locator: FakeLocator(address: nil),
                           store: HubDeviceStore(url: dir.appending(path: "\(UUID().uuidString)/d.json")))
        let model = TasksModel()
        await model.loadUntilHubAnswers(hub: hub) { _ in throw CancellationError() }
        #expect(model.hubDown)
        #expect(model.error == "This Mac's hub isn't running. Start it in Settings › Remote.")
    }

    @Test func anUpstreamGithubFailureMarksRowsStale() async throws {
        /// `gh auth status` timed out: no `env_blocked`, no per-repo
        /// failures — but the GitHub list never refreshed, so the rows
        /// are last-known and must say so. `githubSetup` stays nil: a
        /// failed probe isn't a login problem.
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks"
                ? (200, Data(#"{"tasks": [{"id": "github:issue:o/r:5", "source": "github", "kind": "issue", "repo": "o/r", "ref": "5", "title": "Fix the thing", "state": "open"}], "machine": "kai", "machine_id": "m-1", "github": {"status": "upstream-error", "error": "gh auth status timed out"}, "linear": {"status": "disabled"}}"#.utf8))
                : (404, Data())
        }
        let model = TasksModel()
        await model.load(hub: try hub())
        #expect(model.githubSetup == nil)
        #expect(model.envBlocked == nil)
        #expect(model.failedRepos.isEmpty)
        #expect(model.githubFailed == "gh auth status timed out")
        #expect(model.stale(model.tasks[0]))
    }

    @Test func aHealthyGithubLeavesRowsFresh() async throws {
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks" ? (200, Data(Self.listJSON.utf8)) : (404, Data())
        }
        let model = TasksModel()
        await model.load(hub: try hub())
        #expect(model.githubFailed == nil)
        #expect(model.stale(model.tasks[0]) == false)
    }

    /// The page reply with one Notion row and the given `notion` block.
    nonisolated private static func notionReply(_ notion: String) -> Data {
        Data(#"{"tasks": [{"id": "notion:page:roadmap:p1", "source": "notion", "kind": "page", "project": "Roadmap", "ref": "p1", "title": "Plan Q3", "state": "open"}], "machine": "kai", "machine_id": "m-1", "github": {"status": "ok"}, "notion": "#.utf8)
            + Data(notion.utf8) + Data("}".utf8)
    }

    @Test func aNotionRowIsStaleWhileNotionFailsAndFreshOnceItRecovers() async throws {
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks"
                ? (200, Self.notionReply(#"{"status": "upstream-error", "error": "Notion answered HTTP 502"}"#))
                : (404, Data())
        }
        let model = TasksModel()
        let hub = try hub()
        await model.load(hub: hub)
        #expect(model.notionBlocked == "Notion answered HTTP 502")
        #expect(model.stale(model.tasks[0]))
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks"
                ? (200, Self.notionReply(#"{"status": "ok", "login": "Kai"}"#))
                : (404, Data())
        }
        await model.load(hub: hub, refresh: true)
        #expect(model.notionBlocked == nil)
        #expect(model.stale(model.tasks[0]) == false)
    }

    @Test func aFailedRefreshKeepsTheRowsItHad() async throws {
        HubStub.handler = { _ in (200, Data(Self.listJSON.utf8)) }
        let model = TasksModel()
        let hub = try hub()
        await model.load(hub: hub)
        HubStub.handler = { _ in
            (500, Data(#"{"error": {"code": "tasks_failed", "message": "Gh died"}}"#.utf8))
        }
        await model.load(hub: hub, refresh: true)
        #expect(model.tasks.map(\.id) == ["github:issue:o/r:5"])
        #expect(model.error == "Gh died")
        #expect(model.loaded)
    }

    @Test func linkAndUnlinkReloadThroughTheStore() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            switch req.url?.path {
            case "/v1/app/tasks":
                return (200, Data(Self.listJSON.utf8))
            // URL.path is percent-decoded: the key's `:` arrives decoded.
            case "/v1/sessions/claude:b/task":
                return (200, Data(#"{"ok": true}"#.utf8))
            default: return (404, Data())
            }
        }
        let model = TasksModel()
        let hub = try hub()
        await model.load(hub: hub)
        let task = try #require(model.tasks.first)

        // Unlink sends the task ref, then a fresh list lands.
        #expect(await model.unlink(task: task, key: "claude:b", hub: hub) == nil)
        let del = try #require(HubStub.requests.first { $0.httpMethod == "DELETE" })
        #expect(del.url?.absoluteString.contains("/v1/sessions/claude%3Ab/task") == true)
        #expect(HubStub.requests.contains { $0.url?.query?.contains("refresh=1") == true })

        // Linking a session the same way reloads it back in.
        HubStub.requests = []
        #expect(await model.link(task: task, key: "claude:b", hub: hub) == nil)
        #expect(HubStub.requests.first { $0.httpMethod == "POST" } != nil)
        #expect(model.actionError == nil)
        #expect(model.busy.isEmpty)
    }

    @Test func aRefusedLinkSurfacesTheHubsWords() async throws {
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks"
                ? (200, Data(Self.listJSON.utf8))
                : (404, Data(#"{"error": {"code": "unknown_task", "message": "No such task."}}"#.utf8))
        }
        let model = TasksModel()
        let hub = try hub()
        await model.load(hub: hub)
        let task = try #require(model.tasks.first)
        let failure = await model.link(task: task, key: "claude:b", hub: hub)
        #expect(failure == "No such task.")
        #expect(model.actionError == "No such task.")
        #expect(model.busy.isEmpty)
    }
}
}

/// The waits `loadUntilHubAnswers` asked for, in order.
private actor WaitLog {
    private(set) var all: [Duration] = []
    var count: Int { all.count }
    func add(_ wait: Duration) { all.append(wait) }
}
