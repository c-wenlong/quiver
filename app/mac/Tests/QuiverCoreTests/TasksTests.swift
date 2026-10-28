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
         "state": "in_progress", "sessions": ["claude@/w:abc"],
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
        #expect(!TaskFilter.prOpen.includes(row("g", kind: "pull", state: "open")))
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
        #expect(sections.map(\.name) == ["o/s", "o/r", "QUI", "Other"])
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

    @Test func sourceLineAndLabels() {
        #expect(row("a", kind: "issue").sourceLine == "GitHub issue · o/r")
        #expect(row("a", kind: "pull").sourceLine == "GitHub pull request · o/r")
        #expect(row("a", source: "linear", repo: nil, project: "QUI").sourceLine == "Linear · QUI")
        #expect(row("a", state: "in_progress").stateLabel == "In progress")
        #expect(row("a", state: "pr_open").stateLabel == "PR open")
        #expect(row("a", state: "weird").stateLabel == "Open")
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
    private func row(kind: String? = "issue", state: String? = "open") -> HubTaskRow {
        HubTaskRow(id: "t", source: "github", kind: kind, ref: "5",
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
            == [step("Open", true, false), step("In progress", true, false),
                step("PR open", true, true), step("Merged", false, false)])
        #expect(TaskStepper.steps(for: row(state: "merged")).last
            == step("Merged", true, true))
        #expect(TaskStepper.steps(for: row()).first == step("Open", true, true))
    }

    @Test func pullLadderSkipsThePrRung() {
        let steps = TaskStepper.steps(for: row(kind: "pull", state: "in_progress"))
        #expect(steps.map(\.title) == ["Open", "In progress", "Merged"])
        #expect(steps[1] == step("In progress", true, true))
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

    /// A request's query as a name → value dict.
    private func query(_ request: URLRequest) -> [String: String] {
        let items = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        return Dictionary(items.map { ($0.name, $0.value ?? "") }) { _, b in b }
    }

    /// A request's JSON body with nested objects, where
    /// `HubStub.body(_:)` flattens to strings.
    private func body(_ request: URLRequest) throws -> [String: Any] {
        var data = request.httpBody ?? Data()
        if data.isEmpty, let stream = request.httpBodyStream {
            stream.open()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let n = stream.read(&buffer, maxLength: buffer.count)
                guard n > 0 else { break }
                data.append(buffer, count: n)
            }
            stream.close()
        }
        return try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
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
        #expect(query(req) == ["refresh": "1"])
    }

    @Test func taskHarnessesAskTheSameRoute() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/app/tasks"
                ? (200, Data(#"{"harnesses": [{"name": "claude", "display_name": "Claude Code", "tracked": true}], "machine": "kai"}"#.utf8))
                : (404, Data())
        }
        let (harnesses, machine) = try await client().taskHarnesses()
        #expect(harnesses.map(\.name) == ["claude"])
        #expect(harnesses.first?.displayName == "Claude Code")
        #expect(harnesses.first?.tracked == true)
        #expect(machine == "kai")
        #expect(HubStub.requests.first.map(query) == ["harnesses": "1"])
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

    private func query(_ request: URLRequest) -> [String: String] {
        let items = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)?.queryItems ?? []
        return Dictionary(items.map { ($0.name, $0.value ?? "") }) { _, b in b }
    }

    @Test func launchBodyCarriesTheHarnessCwdAndPrompt() {
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go") as? [String: String]
            == ["harness": "claude", "cwd": "/p", "prompt": "go"])
        // A satellite is named; the hub itself is the omitted default.
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go",
                                     machine: "m2")["machine"] as? String == "m2")
        #expect(HubClient.launchBody(harness: "claude", cwd: "/p", prompt: "go")["machine"] == nil)
    }

    @Test func startSessionPostsAndReadsTheLaunch() async throws {
        HubStub.requests = []
        HubStub.handler = { req in
            req.url?.path == "/v1/sessions" && req.httpMethod == "POST"
                ? (202, Data(#"{"launch_id": "l-9", "state": "pending"}"#.utf8))
                : (404, Data())
        }
        let launch = try await client().startSession(harness: "claude", cwd: "/p",
                                                     prompt: "fix it", machine: "m2")
        #expect(launch.launchId == "l-9")
        #expect(launch.state == "pending")
        let sent = HubStub.body(try #require(HubStub.requests.first))
        #expect(sent == ["harness": "claude", "cwd": "/p", "prompt": "fix it", "machine": "m2"])
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
                                        harness: "codex", prompt: "review it")
        let sent = HubStub.body(try #require(HubStub.requests.first))
        #expect(sent == ["harness": "codex", "prompt": "review it"])
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
        #expect(HubStub.requests.first.map(query) == ["path": "/Users/k"])
        // The default is the hub user's home: no query.
        _ = try await client().folders()
        #expect(HubStub.requests.last.map(query) == [:])
    }
}

/// `TasksModel` over a stubbed hub: the load's fills, a failed refresh
/// keeping the rows it had, and link/unlink writing the store.
@MainActor
@Suite struct TasksModelTests {
    let dir = FileManager.default.temporaryDirectory.appending(path: "hub-\(UUID().uuidString)")

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
     "linear": {"status": "auth-required", "error": "no key"}}
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
        #expect(model.error == nil)
        // The failed repo's rows are last-known; a healthy one's aren't.
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
