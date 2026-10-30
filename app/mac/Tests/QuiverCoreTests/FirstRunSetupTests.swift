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
    /// `swe setup --hub=…`: the preview it answers with, or the refusal it
    /// exits 1 with on stderr.
    var cloned: String = "{}"
    var cloneRefusal: String?
    private(set) var calls: [[String]] = []

    init(preview: String, applied: String = "{}") {
        self.preview = preview
        self.applied = applied
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        if arguments.contains(where: { $0.hasPrefix("--hub=") }) {
            if let cloneRefusal {
                return CommandResult(status: 1, stdout: Data(), stderr: "  \(cloneRefusal)\n")
            }
            return CommandResult(status: 0, stdout: Data(#"{"schema": 1, "data": \#(cloned)}"#.utf8), stderr: "")
        }
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

/// The preview on a Mac whose ~/.quiver has no hub yet.
private let hubPreviewJSON = previewJSON.replacingOccurrences(
    of: #""skills": [],"#,
    with: #""skills": [], "hub": {"can_clone": true, "demo_url": null, "cloned": null},"#)

/// What `swe setup --hub=… --json` answers once it cloned: the hub's own
/// AGENTS.md is there, so nothing is offered to adopt.
private let clonedPreviewJSON = """
{"harness": [], "mcp": [], "skills": [],
 "instructions": {
   "agents_file": "/h/.quiver/AGENTS.md", "agents_file_exists": true,
   "agents_file_customised": true, "rows": [], "adopt_candidates": []},
 "hub": {"can_clone": false, "demo_url": null,
         "cloned": {"url": "https://github.com/me/hub.git", "path": "/h/.quiver",
                    "marketplaces": [{"harness": "claude", "marketplace": "mine", "error": null},
                                     {"harness": "codex", "marketplace": "mine",
                                      "error": "codex is not on PATH"}]}}}
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

    @Test func phoneAccessIsTheLastStepAndContinueStopsThere() async {
        let runner = SetupRunner(preview: previewJSON, applied: applied(leftAlone: false))
        let setup = model(runner, starred: ["claude"])
        await setup.load()
        for _ in 0..<6 { await setup.next(order: ["claude"]) }
        #expect(setup.step == .phone)
        #expect(FirstRunSetupModel.Step.allCases.last == .phone)
        #expect(setup.step.number == 5)
        // Phone access runs nothing: the only swe calls are setup's own.
        #expect(runner.calls.allSatisfy { $0.first == "setup" })
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

    @Test func changedPicksAfterGoingBackRunSetupAgainWithoutAdopt() async {
        let runner = SetupRunner(preview: previewJSON, applied: applied(leftAlone: false))
        let setup = model(runner, starred: ["claude"])
        await setup.load()
        await setup.next(order: ["codex", "claude"])
        await setup.next(order: ["codex", "claude"])
        #expect(setup.step == .look)

        // A run sent without --adopt reports adopted: null, as swe does.
        runner.applied = applied(leftAlone: false).replacingOccurrences(
            of: #""adopted": "/h/.claude/CLAUDE.md""#, with: #""adopted": null"#)
        setup.back()
        setup.back()
        setup.toggle("codex")
        #expect(setup.needsApply)
        await setup.next(order: ["codex", "claude"])
        await setup.next(order: ["codex", "claude"])
        #expect(setup.step == .look)
        let applies = runner.calls.filter { $0.contains("--apply") }
        #expect(applies.count == 2)
        #expect(applies.last == ["setup", "--apply", "--json", "--starred=codex,claude"],
                "the second run stars the new picks and adopts nothing")

        // The second run's outcome said adopted: null; a third run still
        // must not adopt again.
        setup.back()
        setup.back()
        setup.toggle("claude")
        await setup.next(order: ["codex", "claude"])
        await setup.next(order: ["codex", "claude"])
        #expect(runner.calls.filter { $0.contains("--apply") }.last
                == ["setup", "--apply", "--json", "--starred=codex"])
    }

    @Test func picksSweCannotStarHoldTheStep() async {
        let runner = SetupRunner(preview: previewJSON, applied: """
            {"applied": {"harness": [], "mcp": [], "skills": [],
             "starred": {"starred": ["claude"], "registered": [], "unknown": ["antigravity"]},
             "links": {"adopted": null, "rows": []}}}
            """)
        let setup = model(runner, starred: ["claude", "antigravity"])
        await setup.load()
        await setup.next(order: [])
        await setup.next(order: [])
        #expect(setup.step == .links)
        #expect(setup.outcome?.unstarred == ["antigravity"])
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

    @Test func theHubFieldShowsOnlyWhileQuiverHasNoHub() async {
        let fresh = model(SetupRunner(preview: hubPreviewJSON))
        await fresh.load()
        #expect(fresh.offersHub)
        guard case .loaded(let preview) = fresh.preview else { return }
        #expect(preview.hub?.demoUrl == nil)

        // An swe from before --hub sends no hub key: no field.
        let old = model(SetupRunner(preview: previewJSON))
        await old.load()
        #expect(!old.offersHub)

        let hubThere = model(SetupRunner(preview: hubPreviewJSON.replacingOccurrences(
            of: #""can_clone": true"#, with: #""can_clone": false"#)))
        await hubThere.load()
        #expect(!hubThere.offersHub)
    }

    @Test func cloneRunsSweSetupHubAndShowsTheClonedHub() async {
        let runner = SetupRunner(preview: hubPreviewJSON, applied: applied(leftAlone: false))
        runner.cloned = clonedPreviewJSON
        let setup = model(runner)
        await setup.load()
        #expect(setup.start == .file("/h/.claude/CLAUDE.md"))
        setup.hubURL = "  https://me:tok@github.com/me/hub.git \n"
        await setup.cloneHub()

        #expect(runner.calls.last == ["setup", "--hub=https://me:tok@github.com/me/hub.git",
                                      "--json", "--non-interactive"])
        #expect(setup.hubError == nil)
        #expect(!setup.cloning)
        #expect(setup.hubURL.isEmpty)
        #expect(setup.cloned?.url == "https://github.com/me/hub.git")
        #expect(setup.cloned?.marketplaces.compactMap(\.error) == ["codex is not on PATH"])
        #expect(!setup.offersHub)
        // The hub brought its own AGENTS.md: nothing to adopt, so the apply
        // that follows sends no --adopt.
        #expect(setup.start == .seed)
        #expect(setup.untouchedCandidates.isEmpty)
        await setup.next(order: [])
        await setup.next(order: [])
        #expect(runner.calls.last == ["setup", "--apply", "--json", "--starred="])
    }

    @Test func aRefusedCloneSaysWhyAndKeepsTheURL() async {
        let runner = SetupRunner(preview: hubPreviewJSON)
        runner.cloneRefusal = "can't reach https://github.com/me/nope.git: repository not found"
        let setup = model(runner)
        await setup.load()
        setup.hubURL = "https://github.com/me/nope.git"
        await setup.cloneHub()
        #expect(setup.hubError == "can't reach https://github.com/me/nope.git: repository not found")
        #expect(setup.hubURL == "https://github.com/me/nope.git")
        #expect(setup.offersHub, "the field stays to try again")
        #expect(setup.cloned == nil)
    }

    @Test func aBlankURLRunsNothing() async {
        let runner = SetupRunner(preview: hubPreviewJSON)
        let setup = model(runner)
        await setup.load()
        setup.hubURL = "   "
        await setup.cloneHub()
        #expect(runner.calls.allSatisfy { !$0.contains(where: { $0.hasPrefix("--hub") }) })
    }

    @Test func anApplyStepErrorIsShownAndHoldsTheStep() async {
        // A step that failed inside --apply lands in applied.errors with
        // exit 1; the envelope still decodes, so the error must surface.
        let runner = SetupRunner(preview: previewJSON, applied: """
            {"applied": {"harness": ["claude"], "mcp": [], "skills": [],
             "errors": ["mcp: disk full"],
             "links": {"adopted": null, "rows": []}}}
            """)
        runner.appliedStatus = 1
        let setup = model(runner)
        await setup.load()
        await setup.next(order: [])
        await setup.next(order: [])
        #expect(setup.step == .links)
        #expect(setup.applyError == "mcp: disk full")
    }
}
