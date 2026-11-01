import Foundation
import Synchronization
import Testing
@testable import QuiverCore

/// Resolve-an-issue (#816): the `swe gh issue view|comment` contracts,
/// the hub's `POST /v1/issues/resolve` body and launch polling, the
/// harness choice, and the row a launched session's chat opens on.
///
/// `swe-gh-issue-view.json` and `swe-gh-issue-comment.json` are built to
/// the shape the hub-side change emits (agreed on #816) rather than
/// captured, since that `swe` isn't on main yet. Recapture them once it is.
@Suite struct IssueContractTests {
    private func client(_ fixtureName: String) throws -> (SweClient, FakeRunner) {
        let runner = FakeRunner(.init(status: 0, stdout: try fixture(fixtureName), stderr: ""))
        return (SweClient(runner: runner), runner)
    }

    @Test func issueViewDecodesBodyCommentsAndWorktree() async throws {
        let (swe, runner) = try client("swe-gh-issue-view")
        let reply = try await swe.ghIssueView(repo: "c-wenlong/quiver", number: 816)
        #expect(runner.calls.last == ["gh", "issue", "view", "816", "--repo=c-wenlong/quiver", "--json"])
        #expect(reply.github?.status == .ok)
        #expect(reply.status == "ok")
        let issue = try #require(reply.issue)
        #expect(issue.number == 816)
        #expect(issue.state == .open)
        #expect(issue.labels == ["enhancement"])
        #expect(issue.assignees.isEmpty)
        #expect(issue.body.hasPrefix("## Problem"))
        #expect(issue.createdAt?.date != nil)
        #expect(issue.comments.count == 2)
        #expect(issue.comments[0].author == "c-wenlong")
        #expect(issue.comments[1].createdAt?.date != nil)
        #expect(issue.comments[1].url?.hasSuffix("issuecomment-2") == true)
        #expect(issue.branch == "quiver/issue-816")
        #expect(issue.worktree == "/Users/k/.swe/worktrees/c-wenlong__quiver-issue816")
        #expect(issue.suggestedPrompt?.contains("Closes #816") == true)
    }

    @Test func issueViewToleratesMissingListsAndAFailure() throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let trimmed = try decoder.decode(GhIssueDetail.self, from: Data(
            #"{"number": 3, "title": "t", "state": "closed"}"#.utf8))
        #expect(trimmed.comments.isEmpty)
        #expect(trimmed.labels.isEmpty)
        #expect(trimmed.body.isEmpty)
        #expect(trimmed.worktree == nil)
        let failed = try decoder.decode(GhIssueView.self, from: Data(
            #"{"repo": "o/r", "status": "upstream-error", "issue": null, "error": "no issue found"}"#.utf8))
        #expect(failed.issue == nil)
        #expect(failed.github == nil)
        #expect(failed.error == "no issue found")
    }

    @Test func commentPostsTheBodyAndDecodesTheComment() async throws {
        let (swe, runner) = try client("swe-gh-issue-comment")
        let reply = try await swe.ghIssueComment(repo: "c-wenlong/quiver", number: 816,
                                                 body: "Start with --the Mac app")
        // The text goes on stdin, never argv, where other processes can read it.
        #expect(runner.calls.last == ["gh", "issue", "comment", "816", "--repo=c-wenlong/quiver",
                                      "--body-file=-", "--json"])
        #expect(runner.inputs.last == Data("Start with --the Mac app".utf8))
        #expect(reply.issue == 816)
        #expect(reply.error == nil)
        #expect(reply.comment?.author == "c-wenlong")
        #expect(reply.comment?.createdAt?.date != nil)
    }

    @Test func aRefusedCommentStillDecodes() async throws {
        let body = #"{"schema": 1, "data": {"github": {"status": "auth-required", "login": null, "error": null}, "repo": "o/r", "status": "auth-required", "issue": 4, "error": "gh isn't signed in"}}"#
        let swe = SweClient(runner: FakeRunner(.init(status: 1, stdout: Data(body.utf8), stderr: "")))
        let reply = try await swe.ghIssueComment(repo: "o/r", number: 4, body: "x")
        #expect(reply.comment == nil)
        #expect(reply.error == "gh isn't signed in")
        #expect(reply.github?.status == .authRequired)
    }
}

/// The model's issue slots and comment write, over canned replies.
@MainActor
@Suite struct IssueModelTests {
    final class Router: CommandRunning, @unchecked Sendable {
        var replies: [String: CommandResult]
        private(set) var calls: [[String]] = []
        private(set) var inputs: [Data?] = []
        private let lock = NSLock()
        init(_ replies: [String: CommandResult]) { self.replies = replies }
        func run(_ arguments: [String]) async throws -> CommandResult {
            lock.withLock { calls.append(arguments) }
            let line = arguments.joined(separator: " ")
            for (prefix, reply) in replies where line.hasPrefix(prefix) { return reply }
            return .init(status: 1, stdout: Data(), stderr: "no route")
        }
        func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
            lock.withLock { inputs.append(input) }
            return try await run(arguments)
        }
    }

    private func ok(_ name: String) throws -> CommandResult {
        .init(status: 0, stdout: try fixture(name), stderr: "")
    }

    @Test func detailLoadsIntoTheKeyedSlot() async throws {
        let swe = SweClient(runner: Router(["gh issue view 816 --repo=c-wenlong/quiver": try ok("swe-gh-issue-view")]))
        let model = GitHubModel()
        await model.loadIssueDetail(repo: "c-wenlong/quiver", number: 816, swe: swe)
        guard case .loaded(let issue) = model.issueDetails["c-wenlong/quiver#816"] else {
            Issue.record("expected a loaded detail"); return
        }
        #expect(issue.comments.count == 2)
        #expect(model.env?.login == "c-wenlong")
    }

    @Test func aFailedViewLandsTyped() async {
        let body = #"{"schema": 1, "data": {"github": {"status": "ok", "login": "me", "error": null}, "repo": "o/r", "status": "not-found", "issue": null, "error": "no issue 9"}}"#
        let swe = SweClient(runner: Router(["gh issue view 9": .init(status: 0, stdout: Data(body.utf8), stderr: "")]))
        let model = GitHubModel()
        await model.loadIssueDetail(repo: "o/r", number: 9, swe: swe)
        #expect(model.issueDetails["o/r#9"] == .failed("no issue 9"))
    }

    @Test func aPostedCommentReadsTheIssueAgain() async throws {
        let router = Router([
            "gh issue comment 816": try ok("swe-gh-issue-comment"),
            "gh issue view 816": try ok("swe-gh-issue-view"),
        ])
        let model = GitHubModel()
        let error = await model.commentOnIssue(repo: "c-wenlong/quiver", number: 816,
                                               body: "  hello  ", swe: SweClient(runner: router))
        #expect(error == nil)
        #expect(router.calls.first?.contains("--body-file=-") == true)
        #expect(router.inputs.first == Data("hello".utf8))
        #expect(router.calls.last?.prefix(3) == ["gh", "issue", "view"])
        #expect(!model.commenting(repo: "c-wenlong/quiver", number: 816))
        if case .loaded = model.issueDetails["c-wenlong/quiver#816"] {} else {
            Issue.record("expected the detail read again")
        }
    }

    @Test func aRefusedCommentReturnsTheReasonAndSkipsTheRead() async {
        let body = #"{"schema": 1, "data": {"repo": "o/r", "status": "upstream-error", "issue": 4, "error": "issue is locked"}}"#
        let router = Router(["gh issue comment 4": .init(status: 1, stdout: Data(body.utf8), stderr: "")])
        let model = GitHubModel()
        let error = await model.commentOnIssue(repo: "o/r", number: 4, body: "x", swe: SweClient(runner: router))
        #expect(error == "issue is locked")
        #expect(router.calls.count == 1)
    }

    /// Holds the first `gh issue view` at `gate` until the test opens it;
    /// later ones answer at once with a title that says which read it was.
    final class GatedRunner: CommandRunning, Sendable {
        let gate = HoldGate()
        private let count = Mutex(0)

        var views: Int { count.withLock { $0 } }

        private func body(_ title: String) -> Data {
            Data(#"{"schema": 1, "data": {"repo": "o/r", "status": "ok", "issue": {"number": 5, "title": "\#(title)", "state": "open"}}}"#.utf8)
        }

        func run(_ arguments: [String]) async throws -> CommandResult {
            let first = count.withLock { n -> Bool in
                n += 1
                return n == 1
            }
            if first {
                try await gate.pass()
                return .init(status: 0, stdout: body("old"), stderr: "")
            }
            return .init(status: 0, stdout: body("new"), stderr: "")
        }
        func run(_ arguments: [String], input: Data?) async throws -> CommandResult {
            try await run(arguments)
        }
    }

    @Test func aSecondOpenerJoinsTheReadInFlight() async {
        let runner = GatedRunner()
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        async let first: Void = model.loadIssueDetail(repo: "o/r", number: 5, swe: swe)
        #expect(await runner.gate.arrived())
        async let second: Void = model.loadIssueDetail(repo: "o/r", number: 5, swe: swe)
        runner.gate.open()
        _ = await (first, second)
        #expect(runner.views == 1)
        guard case .loaded(let issue) = model.issueDetails["o/r#5"] else {
            Issue.record("expected the joined read to land"); return
        }
        #expect(issue.title == "old")
    }

    @Test func aFreshReadWinsOverAnOlderSlowOne() async {
        let runner = GatedRunner()
        let swe = SweClient(runner: runner)
        let model = GitHubModel()
        async let slow: Void = model.loadIssueDetail(repo: "o/r", number: 5, swe: swe)
        #expect(await runner.gate.arrived())
        await model.loadIssueDetail(repo: "o/r", number: 5, swe: swe, fresh: true)
        runner.gate.open()
        await slow
        #expect(runner.views == 2)
        guard case .loaded(let issue) = model.issueDetails["o/r#5"] else {
            Issue.record("expected a loaded detail"); return
        }
        #expect(issue.title == "new")
    }

    @Test func aBlankCommentPostsNothing() async {
        let router = Router([:])
        let error = await GitHubModel().commentOnIssue(repo: "o/r", number: 4, body: " \n ",
                                                       swe: SweClient(runner: router))
        #expect(error == nil)
        #expect(router.calls.isEmpty)
    }
}

@Suite struct IssueResolveTests {
    @Test func resolveBodyCarriesOnlyTheTextGiven() {
        let full = HubClient.resolveIssueBody(repo: "o/r", number: 7, harness: "claude",
                                              prompt: "Fix it", context: "Use the v2 API",
                                              model: "sonnet")
        #expect(full["repo"] as? String == "o/r")
        #expect(full["number"] as? Int == 7)
        #expect(full["harness"] as? String == "claude")
        #expect(full["prompt"] as? String == "Fix it")
        #expect(full["context"] as? String == "Use the v2 API")
        #expect(full["model"] as? String == "sonnet")
        let bare = HubClient.resolveIssueBody(repo: "o/r", number: 7, harness: "codex",
                                              prompt: "  ", context: nil)
        #expect(Set(bare.keys) == ["repo", "number", "harness"])
        // A blank or whitespace model is no pick: the key stays out,
        // never a token the hub would refuse.
        #expect(HubClient.resolveIssueBody(repo: "o/r", number: 7, harness: "claude",
                                           prompt: nil, context: nil, model: " ")["model"] == nil)
        let trimmed = HubClient.resolveIssueBody(repo: "o/r", number: 7, harness: "claude",
                                                 prompt: nil, context: nil, model: " opus ")
        #expect(trimmed["model"] as? String == "opus")
    }

    @Test func offeredHarnessesComeFromTheHubsOwnRow() {
        let machines = [
            HubMachine(id: "sat", launchable: ["gemini"]),
            HubMachine(id: "mac", harnesses: ["claude", "codex", "pi"], launchable: ["claude", "codex"]),
        ]
        #expect(IssueResolve.offeredHarnesses(machines: machines, localMachineId: "mac",
                                              fallback: ["x"]) == ["claude", "codex"])
        // An older hub row without `launchable` offers what it detected.
        #expect(IssueResolve.offeredHarnesses(machines: [HubMachine(id: "mac", harnesses: ["claude"])],
                                              localMachineId: "mac", fallback: []) == ["claude"])
        // No id to match: the only row, else the local fallback.
        #expect(IssueResolve.offeredHarnesses(machines: [machines[1]], localMachineId: nil,
                                              fallback: []) == ["claude", "codex"])
        #expect(IssueResolve.offeredHarnesses(machines: machines, localMachineId: nil,
                                              fallback: ["claude"]) == ["claude"])
    }

    @Test func hubMachinePicksTheHubsOwnRow() {
        let machines = [
            HubMachine(id: "sat", launchable: ["gemini"]),
            HubMachine(id: "mac", launchable: ["claude"], modelAtLaunch: ["claude"],
                       lastModels: ["claude": "sonnet"]),
        ]
        // The resolve launch lands on the hub, so the row is the one
        // this Mac's machine.json names — a satellite's fields never
        // stand in for it.
        #expect(IssueResolve.hubMachine(machines: machines, localMachineId: "mac")?.id == "mac")
        #expect(IssueResolve.hubMachine(machines: machines, localMachineId: "sat")?.id == "sat")
        // One row and no id to match: that row is the hub's.
        #expect(IssueResolve.hubMachine(machines: [machines[1]], localMachineId: nil)?.id == "mac")
        // No match and no single row: the hub's fields are unknown.
        #expect(IssueResolve.hubMachine(machines: machines, localMachineId: nil) == nil)
    }

    private func session(_ harness: String, _ cwd: String?, _ age: TimeInterval) -> SessionInfo {
        SessionInfo(id: UUID().uuidString, harness: harness, agent: harness, cwd: cwd, title: nil,
                    titleSource: nil, lastActiveAt: Date(timeIntervalSince1970: 1_800_000_000 - age),
                    status: nil, resume: nil)
    }

    @Test func defaultHarnessIsTheLastOneUsedInTheRepo() {
        let offered = ["claude", "codex", "pi"]
        let sessions = [
            session("pi", "/Users/k/elsewhere", 10),
            session("codex", "/Users/k/src/quiver/app", 60),
            session("claude", "/Users/k/.swe/worktrees/c-wenlong__quiver-pr459", 600),
        ]
        #expect(IssueResolve.defaultHarness(offered: offered, sessions: sessions,
                                            repo: "c-wenlong/quiver") == "codex")
        // A harness the hub can't start is passed over.
        #expect(IssueResolve.defaultHarness(offered: ["claude", "pi"], sessions: sessions,
                                            repo: "c-wenlong/quiver") == "claude")
        // Nothing ran in this repo: the first offered.
        #expect(IssueResolve.defaultHarness(offered: offered, sessions: sessions,
                                            repo: "o/other") == "claude")
        #expect(IssueResolve.defaultHarness(offered: [], sessions: sessions, repo: "o/r") == nil)
    }

    @Test func repoFoldersMatchCheckoutsAndWorktrees() {
        #expect(IssueResolve.inRepo("/Users/k/src/Quiver", repo: "c-wenlong/quiver"))
        #expect(IssueResolve.inRepo("/Users/k/.swe/worktrees/c-wenlong__quiver-issue816", repo: "c-wenlong/quiver"))
        #expect(IssueResolve.inRepo("/Users/k/src/quiver/.claude/worktrees/agent-1", repo: "c-wenlong/quiver"))
        #expect(!IssueResolve.inRepo("/Users/k/src/quiverish", repo: "c-wenlong/quiver"))
        #expect(!IssueResolve.inRepo("/Users/k/src/app", repo: "bad-repo-name"))
    }

    @Test func contextPostsOnlyWithTextAndTheBoxTicked() {
        #expect(IssueResolve.postsComment(context: "Use v2", ticked: true))
        #expect(!IssueResolve.postsComment(context: "Use v2", ticked: false))
        #expect(!IssueResolve.postsComment(context: "  \n", ticked: true))
        // A retried Start doesn't post the same context twice; new text does.
        #expect(!IssueResolve.postsComment(context: "Use v2 ", ticked: true, posted: "Use v2"))
        #expect(IssueResolve.postsComment(context: "Use v3", ticked: true, posted: "Use v2"))
    }

    @Test func localMachineIdReadsMachineJson() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let home = temp.url
        #expect(IssueResolve.localMachineId(home: home) == nil)
        let dir = home.appending(path: ".swe/data/remote")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try Data(#"{"id": "4e72"}"#.utf8).write(to: dir.appending(path: "machine.json"))
        #expect(IssueResolve.localMachineId(home: home) == "4e72")
    }

    @Test func launchRepliesMapToPhases() {
        #expect(IssueLaunchFlow.outcome(HubLaunchStatus(launchId: "l1", state: "pending", note: "Cloning mirror…")) == nil)
        // `started` without a key is not an answer yet.
        #expect(IssueLaunchFlow.outcome(HubLaunchStatus(launchId: "l1", state: "started")) == nil)
        #expect(IssueLaunchFlow.outcome(HubLaunchStatus(launchId: "l1", state: "started", key: "claude:s1"))
            == .started("claude:s1"))
        #expect(IssueLaunchFlow.outcome(HubLaunchStatus(launchId: "l1", state: "failed", note: "tmux missing"))
            == .failed("The session didn't start: tmux missing"))
        #expect(IssueLaunchFlow.outcome(HubLaunchStatus(launchId: "l1", state: "failed"))
            == .failed("The session didn't start."))
        #expect(IssueLaunchFlow.outcome(HubLaunchStatus(launchId: "l1", state: "untracked"))
            == .unconfirmed(IssueLaunchFlow.untrackedText))
        #expect(IssueLaunchFlow.gaveUp(queued: false) == .unconfirmed(IssueLaunchFlow.slowText))
        #expect(IssueLaunchFlow.gaveUp(queued: true) == .unconfirmed(IssueLaunchFlow.queuedText))
    }

    @Test func chatRowPrefersTheLedgerElseTheHub() {
        let info = SessionInfo(id: "s1", harness: "claude", agent: "Claude Code", cwd: "/w", title: "Known",
                               titleSource: nil, lastActiveAt: .distantPast, status: "done", resume: nil)
        let known = SessionsModel.Row(session: info, usage: nil)
        #expect(SessionsModel.chatRow(for: "claude:s1", rows: [known], hub: nil) == known)
        let hub = HubSessionRow(key: "codex:019a", harness: "codex", agent: "Codex", cwd: "/wt",
                                title: "c-wenlong/quiver#816 Resolve", status: "active")
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        let built = SessionsModel.chatRow(for: "codex:019a", rows: [known], hub: hub, now: now)
        #expect(built?.session.id == "019a")
        #expect(built?.session.harness == "codex")
        #expect(built?.session.title == "c-wenlong/quiver#816 Resolve")
        #expect(built?.session.cwd == "/wt")
        #expect(built?.session.lastActiveAt == now)
        #expect(built?.usage == nil)
        // A hub that didn't answer still opens the chat by key.
        #expect(SessionsModel.chatRow(for: "pi:abc:def", rows: [], hub: nil)?.session.id == "abc:def")
        #expect(SessionsModel.chatRow(for: "nokey", rows: [], hub: nil) == nil)
        #expect(SessionsModel.chatRow(for: ":x", rows: [], hub: nil) == nil)
    }
}

/// The flow end to end over injected post and poll closures: no hub, no
/// clock, `pause` returns at once.
@MainActor
@Suite struct IssueLaunchFlowTests {
    final class Script: @unchecked Sendable {
        var replies: [HubLaunchStatus]
        var notes: [String?] = []
        var fetches = 0
        init(_ replies: [HubLaunchStatus]) { self.replies = replies }
        func next() -> HubLaunchStatus {
            fetches += 1
            return replies.count > 1 ? replies.removeFirst() : replies[0]
        }
    }

    @Test func pendingNotesShowThenTheKeyComesBack() async {
        let flow = IssueLaunchFlow()
        let script = Script([
            HubLaunchStatus(launchId: "l1", state: "pending", note: "Creating worktree quiver/issue-7…"),
            HubLaunchStatus(launchId: "l1", state: "pending", note: "Starting claude…"),
            HubLaunchStatus(launchId: "l1", state: "started", key: "claude:s9"),
        ])
        let key = await flow.run(
            post: { HubLaunchStatus(launchId: "l1", state: "pending", note: "Cloning mirror…") },
            fetch: { id in
                #expect(id == "l1")
                return script.next()
            },
            pause: { script.notes.append(flow.note) })
        #expect(key == "claude:s9")
        #expect(flow.phase == .started("claude:s9"))
        #expect(script.notes == ["Cloning mirror…", "Creating worktree quiver/issue-7…", "Starting claude…"])
        #expect(flow.busy)
    }

    @Test func aFailedLaunchKeepsTheHubsReason() async {
        let flow = IssueLaunchFlow()
        let key = await flow.run(
            post: { HubLaunchStatus(launchId: "l1", state: "pending") },
            fetch: { _ in HubLaunchStatus(launchId: "l1", state: "failed", note: "worktree exists") },
            pause: {})
        #expect(key == nil)
        #expect(flow.phase == .failed("The session didn't start: worktree exists"))
        #expect(!flow.busy)
    }

    @Test func aRefusedPostShowsTheHubError() async {
        let flow = IssueLaunchFlow()
        let key = await flow.run(
            post: { throw HubError(status: 0, code: "hub_down",
                                   message: "This Mac's hub isn't running. Start it in Settings › Remote.") },
            fetch: { _ in Issue.record("no poll after a refused post"); return HubLaunchStatus(launchId: "") },
            pause: {})
        #expect(key == nil)
        #expect(flow.phase == .failed("This Mac's hub isn't running. Start it in Settings › Remote."))
    }

    @Test func pollsRunOutAsUnconfirmed() async {
        let flow = IssueLaunchFlow()
        let script = Script([HubLaunchStatus(launchId: "l1", state: "pending")])
        let key = await flow.run(
            post: { HubLaunchStatus(launchId: "l1", state: "pending") },
            fetch: { _ in script.next() },
            attempts: 3,
            pause: {})
        #expect(key == nil)
        #expect(script.fetches == 3)
        #expect(flow.phase == .unconfirmed(IssueLaunchFlow.slowText))
    }

    @Test func droppedPollsAreRetriedAndA404Ends() async {
        let flow = IssueLaunchFlow()
        var calls = 0
        let key = await flow.run(
            post: { HubLaunchStatus(launchId: "l1", state: "pending") },
            fetch: { _ in
                calls += 1
                if calls == 1 { throw HubError.unreachable }
                throw HubError(status: 404, code: "unknown_launch", message: "The hub forgot this launch.")
            },
            pause: {})
        #expect(key == nil)
        #expect(calls == 2)
        #expect(flow.phase == .failed("The hub forgot this launch."))
    }

    @Test func aFailedCommentWarnsButTheLaunchGoesOn() async {
        let flow = IssueLaunchFlow()
        var order: [String] = []
        let key = await flow.run(
            comment: {
                order.append("comment")
                #expect(flow.phase == .commenting)
                return "issue is locked."
            },
            post: {
                order.append("post")
                return HubLaunchStatus(launchId: "l1", state: "started", key: "codex:1")
            },
            fetch: { _ in HubLaunchStatus(launchId: "l1") },
            pause: {})
        #expect(order == ["comment", "post"])
        #expect(key == "codex:1")
        #expect(flow.commentWarning?.contains("issue is locked.") == true)
        flow.reset()
        #expect(flow.phase == .idle)
        #expect(flow.commentWarning == nil)
    }
}

extension HubStubbed {
    /// The resolve and launch routes over the real transport.
    @Suite struct ResolveRouteTests {
        private func client() -> HubClient {
            HubClient(baseURL: URL(string: "http://127.0.0.1:8477")!, credential: "cred",
                      session: HubStub.session())
        }

        private static func json(_ request: URLRequest) -> [String: Any] {
            (try? JSONSerialization.jsonObject(with: HubStub.bodyData(request))) as? [String: Any] ?? [:]
        }

        @Test func resolvePostsAndPollsTheLaunch() async throws {
            HubStub.requests = []
            HubStub.handler = { req in
                switch (req.httpMethod, req.url?.path) {
                case ("POST", "/v1/issues/resolve"):
                    return (202, Data(#"{"ok": true, "launch_id": "l9", "state": "pending", "note": "Cloning mirror…"}"#.utf8))
                case ("GET", "/v1/launches/l9"):
                    return (200, Data(#"{"launch_id": "l9", "state": "started", "key": "claude:s1", "note": "Started"}"#.utf8))
                case ("GET", "/v1/machines"):
                    return (200, Data(#"{"machines": [{"id": "m1", "name": "mac", "online": true, "launchable": ["claude"], "harness_names": {"claude": "Claude Code"}, "model_at_launch": ["claude"], "last_models": {"claude": "sonnet"}}]}"#.utf8))
                default:
                    return (404, Data())
                }
            }
            let hub = client()
            let accepted = try await hub.resolveIssue(repo: "o/r", number: 7, harness: "claude",
                                                      prompt: "Fix it", context: nil)
            #expect(accepted == HubLaunchStatus(launchId: "l9", state: "pending", note: "Cloning mirror…"))
            let post = try #require(HubStub.requests.first)
            #expect(post.value(forHTTPHeaderField: "Authorization") == "Bearer cred")
            let body = Self.json(post)
            #expect(body["repo"] as? String == "o/r")
            #expect(body["number"] as? Int == 7)
            #expect(body["prompt"] as? String == "Fix it")
            #expect(body["context"] == nil)
            let status = try await hub.launch("l9")
            #expect(status.state == "started")
            #expect(status.key == "claude:s1")
            let machines = try await hub.machines()
            #expect(machines.first?.launchable == ["claude"])
            #expect(machines.first?.modelAtLaunch == ["claude"])
            #expect(machines.first?.lastModels == ["claude": "sonnet"])
        }

        @Test func refusalsReadInTheHubsWords() async {
            HubStub.handler = { _ in
                (422, Data(#"{"error": {"code": "not_pinned", "message": "o/r isn't pinned on this hub"}}"#.utf8))
            }
            do {
                _ = try await client().resolveIssue(repo: "o/r", number: 7, harness: "claude",
                                                    prompt: nil, context: nil)
                Issue.record("expected a refusal")
            } catch let error as HubError {
                #expect(error.code == "not_pinned")
                #expect(error.message == "o/r isn't pinned on this hub")
            } catch {
                Issue.record("unexpected \(error)")
            }
            #expect(HubError.from(status: 422, body: Data(#"{"error": {"code": "gh_unavailable"}}"#.utf8)).message
                == "gh isn't installed or signed in on the hub machine.")
        }
    }
}
