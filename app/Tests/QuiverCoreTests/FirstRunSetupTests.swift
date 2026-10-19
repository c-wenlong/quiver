import Foundation
import Testing
@testable import QuiverCore

/// Answers `swe setup --json` with `preview` and `--apply` with `applied`,
/// recording every argv.
private final class SetupRunner: CommandRunning, @unchecked Sendable {
    var preview: String
    var applied: String
    /// swe exits 1 when linking stopped, with the JSON still on stdout.
    var appliedStatus: Int32 = 0
    private(set) var calls: [[String]] = []

    init(preview: String, applied: String = "{}") {
        self.preview = preview
        self.applied = applied
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        let apply = arguments.contains("--apply")
        let body = apply ? applied : preview
        return CommandResult(status: apply ? appliedStatus : 0, stdout: Data(#"{"schema": 1, "data": \#(body)}"#.utf8), stderr: "")
    }
}

private let previewJSON = """
{"harness": [{"name": "claude", "command": "claude", "path": "/x/claude"}],
 "mcp": [{"name": "linear", "tools": ["claude"]}],
 "skills": [],
 "instructions": {
   "agents_file": "/h/.quiver/AGENTS.md", "agents_file_exists": false,
   "agents_file_customised": false,
   "rows": [{"kind": "instructions", "harness": "claude", "path": "/h/.claude/CLAUDE.md",
             "result": "would-conflict", "detail": "real file, needs --force"}],
   "adopt_candidates": [{"harness": "claude", "path": "/h/.claude/CLAUDE.md"},
                        {"harness": "codex", "path": "/h/.codex/AGENTS.md"}]}}
"""

private func applied(leftAlone: Bool) -> String {
    let rows = leftAlone
        ? #"[{"kind": "instructions", "harness": "codex", "path": "/h/.codex/AGENTS.md", "result": "blocked", "detail": "real file, needs --force"}]"#
        : #"[{"kind": "instructions", "harness": "claude", "path": "/h/.claude/CLAUDE.md", "result": "linked", "detail": ""}]"#
    return """
    {"applied": {"harness": ["claude"], "mcp": ["linear"], "skills": [],
     "starred": {"starred": ["codex", "claude"], "registered": [], "unknown": []},
     "links": {"adopted": "/h/.claude/CLAUDE.md", "scaffold": [], "rows": \(rows)}}}
    """
}

@MainActor
@Suite struct FirstRunSetupTests {
    private func model(_ runner: SetupRunner, starred: Set<String> = []) -> FirstRunSetupModel {
        FirstRunSetupModel(swe: { SweClient(runner: runner) }, starred: starred)
    }

    @Test func offeredOnlyOnAMacWithNoSharedAgentsMdThatHasNotSeenIt() {
        #expect(FirstRun.shouldOffer(done: false, agentsFileExists: false))
        #expect(!FirstRun.shouldOffer(done: false, agentsFileExists: true))
        #expect(!FirstRun.shouldOffer(done: true, agentsFileExists: false))
    }

    @Test func thePreviewDecodesAndYourOwnFileStartsAgentsMd() async {
        let setup = model(SetupRunner(preview: previewJSON))
        await setup.load()
        guard case .loaded(let preview) = setup.preview else {
            Issue.record("preview didn't load: \(setup.preview)")
            return
        }
        #expect(preview.mcp.map(\.name) == ["linear"])
        #expect(preview.instructions?.adoptCandidates.count == 2)
        #expect(setup.start == .file("/h/.claude/CLAUDE.md"))
        #expect(setup.untouchedCandidates.map(\.path) == ["/h/.codex/AGENTS.md"])
    }

    @Test func aCustomisedAgentsMdStartsFromItself() {
        let instructions = SetupPreview.Instructions(
            agentsFile: "/h/.quiver/AGENTS.md", agentsFileExists: true, agentsFileCustomised: true,
            rows: [], adoptCandidates: [.init(harness: "claude", path: "/h/.claude/CLAUDE.md")])
        #expect(FirstRunSetupModel.defaultStart(instructions) == .seed)
        #expect(FirstRunSetupModel.defaultStart(nil) == .seed)
    }

    @Test func pickedAgentsKeepTheGridOrderThenTheRest() {
        let setup = model(SetupRunner(preview: previewJSON), starred: ["claude", "zed", "codex"])
        #expect(setup.starredList(order: ["codex", "cursor", "claude"]) == ["codex", "claude", "zed"])
    }

    @Test func leavingTheLinksStepRunsSetupOnceWithStarsAndAdopt() async {
        let runner = SetupRunner(preview: previewJSON, applied: applied(leftAlone: false))
        let setup = model(runner, starred: ["claude", "codex"])
        await setup.load()
        await setup.next(order: ["codex", "claude"])
        #expect(setup.step == .links)
        await setup.next(order: ["codex", "claude"])
        #expect(setup.step == .look)
        #expect(setup.outcome?.harness == ["claude"])

        let applies = runner.calls.filter { $0.contains("--apply") }
        #expect(applies == [["setup", "--apply", "--json", "--starred=codex,claude",
                             "--adopt=/h/.claude/CLAUDE.md"]])

        setup.back()
        await setup.next(order: ["codex", "claude"])
        #expect(runner.calls.filter { $0.contains("--apply") }.count == 1, "setup runs once")
    }

    @Test func theStarterFileSendsNoAdopt() async {
        let runner = SetupRunner(preview: previewJSON, applied: applied(leftAlone: false))
        let setup = model(runner)
        await setup.load()
        setup.start = .seed
        await setup.next(order: [])
        await setup.next(order: [])
        #expect(runner.calls.last == ["setup", "--apply", "--json", "--starred="])
    }

    @Test func filesLeftAloneHoldTheStepUntilASecondContinue() async {
        let runner = SetupRunner(preview: previewJSON, applied: applied(leftAlone: true))
        let setup = model(runner)
        await setup.load()
        await setup.next(order: [])
        await setup.next(order: [])
        #expect(setup.step == .links)
        #expect(setup.outcome?.leftAlone.map(\.path) == ["/h/.codex/AGENTS.md"])
        await setup.next(order: [])
        #expect(setup.step == .look)
    }

    @Test func aLinkErrorIsShownAndHoldsTheStep() async {
        let runner = SetupRunner(preview: previewJSON, applied: """
            {"applied": {"harness": [], "mcp": [], "skills": [],
             "links": {"adopted": null, "error": "Fix ~/.quiver/.linkignore"}}}
            """)
        runner.appliedStatus = 1
        let setup = model(runner)
        await setup.load()
        await setup.next(order: [])
        await setup.next(order: [])
        #expect(setup.step == .links)
        #expect(setup.applyError == "Fix ~/.quiver/.linkignore")
    }
}
