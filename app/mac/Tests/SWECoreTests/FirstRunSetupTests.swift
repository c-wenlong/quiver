import Foundation
import Testing
@testable import SWECore

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
    var clonedStatus: Int32 = 0
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
            return CommandResult(status: clonedStatus, stdout: Data(#"{"schema": 1, "data": \#(cloned)}"#.utf8),
                                 stderr: clonedStatus == 0 ? "" : "  setup failed: can't read .linkignore\n")
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
   "agents_file": "/h/.swe/AGENTS.md", "agents_file_exists": false,
   "agents_file_customised": false,
   "rows": [{"kind": "instructions", "harness": "claude", "path": "/h/.claude/CLAUDE.md",
             "result": "would-conflict", "detail": "real file, needs --force"}],
   "adopt_candidates": [{"harness": "claude", "path": "/h/.claude/CLAUDE.md"},
                        {"harness": "codex", "path": "/h/.codex/AGENTS.md"}]}}
"""

/// The preview on a Mac whose ~/.swe has no hub yet.
private let hubPreviewJSON = previewJSON.replacingOccurrences(
    of: #""skills": [],"#,
    with: #""skills": [], "hub": {"can_clone": true, "demo_url": null, "cloned": null},"#)

/// What `swe setup --hub=… --json` answers once it cloned: the hub's own
/// AGENTS.md is there, so nothing is offered to adopt.
private let clonedPreviewJSON = """
{"harness": [], "mcp": [], "skills": [],
 "instructions": {
   "agents_file": "/h/.swe/AGENTS.md", "agents_file_exists": true,
   "agents_file_customised": true, "rows": [], "adopt_candidates": []},
 "hub": {"can_clone": false, "demo_url": null,
         "cloned": {"url": "https://github.com/me/hub.git", "path": "/h/.swe",
                    "marketplaces": [{"harness": "claude", "marketplace": "mine", "error": null},
                                     {"harness": "codex", "marketplace": "mine",
                                      "error": "codex is not on PATH"}],
                    "plugins": [{"harness": "claude", "name": "git-helpers", "marketplace": "mine",
                                 "skipped": null, "error": null},
                                {"harness": "claude", "name": "lint", "marketplace": "mine",
                                 "skipped": null, "error": "network is down"}],
                    "mcp": [{"harness": "claude", "name": "context7", "skipped": null,
                             "error": null, "needs": []},
                            {"harness": "claude", "name": "github", "error": null,
                             "skipped": "no value for GITHUB_TOKEN; set it with: swe mcp secret set GITHUB_TOKEN",
                             "needs": ["GITHUB_TOKEN"]},
                            {"harness": "codex", "name": "github", "error": null,
                             "skipped": "no value for GITHUB_TOKEN; set it with: swe mcp secret set GITHUB_TOKEN",
                             "needs": ["GITHUB_TOKEN"]}]}}}
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
            agentsFile: "/h/.swe/AGENTS.md", agentsFileExists: true, agentsFileCustomised: true,
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

    @Test func setupWaitsForTheRegistryGate() async {
        let runner = SetupRunner(preview: previewJSON, applied: applied(leftAlone: false))
        let setup = model(runner, starred: ["claude"])
        await setup.load()
        await setup.next(order: ["claude"])
        #expect(setup.step == .links)
        // A Harnesses page edit holds the gate.
        await HarnessesModel.registryWrites.acquire()
        let leave = Task { await setup.next(order: ["claude"]) }
        // Either setup queues at the gate or, without it, reaches swe.
        while HarnessesModel.registryWrites.waiters == 0,
              !runner.calls.contains(where: { $0.contains("--apply") }) {
            await Task.yield()
        }
        #expect(!runner.calls.contains { $0.contains("--apply") })
        HarnessesModel.registryWrites.release()
        await leave.value
        #expect(runner.calls.filter { $0.contains("--apply") }.count == 1)
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
             "links": {"adopted": null, "error": "Fix ~/.swe/.linkignore"}}}
            """)
        runner.appliedStatus = 1
        let setup = model(runner)
        await setup.load()
        await setup.next(order: [])
        await setup.next(order: [])
        #expect(setup.step == .links)
        #expect(setup.applyError == "Fix ~/.swe/.linkignore")
    }

    @Test func theHubFieldShowsOnlyWhileSWEHasNoHub() async {
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

    @Test func aCloneFromAnOlderSweHasNoInstallNotes() throws {
        let old = #"{"url": "u", "path": "/h/.swe", "marketplaces": []}"#
        let cloned = try JSONDecoder().decode(SetupPreview.Hub.Cloned.self, from: Data(old.utf8))
        #expect(cloned.plugins == nil)
        #expect(cloned.installNotes.isEmpty)
        // It installed and synced nothing, so the card keeps the wording
        // from before that step rather than claiming both ran.
        #expect(cloned.summary == "Its AGENTS.md, skills, plugins and MCP servers are on this Mac now. Set up links them into each agent.")
    }

    @Test func aCloneWithTheInstallStepSaysBothRan() throws {
        let now = #"{"url": "u", "path": "/h/.swe", "marketplaces": [], "plugins": [], "mcp": []}"#
        let cloned = try JSONDecoder().decode(SetupPreview.Hub.Cloned.self, from: Data(now.utf8))
        #expect(cloned.summary == "Its plugins are installed and its MCP servers synced into each agent on this Mac. Set up links its AGENTS.md and skills.")
    }

    @Test func installNotesNameAPluginSetupHadNoTimeForAndAnUnreadableMcpJson() throws {
        let json = """
        {"url": "u", "path": "/h/.swe", "marketplaces": [],
         "plugins": [{"harness": "codex", "name": "had", "marketplace": "m",
                      "skipped": "already installed", "error": null, "retry": null},
                     {"harness": "codex", "name": "late", "marketplace": "m",
                      "skipped": "not attempted: setup ran out of time", "error": null,
                      "retry": "swe plugins install late@m --harness=codex"}],
         "mcp": [{"harness": "", "name": "", "skipped": null, "needs": [],
                  "error": "Cannot parse JSON in /h/.swe/mcp.json: fix or remove the file"}]}
        """
        let cloned = try JSONDecoder().decode(SetupPreview.Hub.Cloned.self, from: Data(json.utf8))
        #expect(cloned.installNotes == [
            "late@m isn't installed in \(HarnessMark.accountName("codex")): not attempted: setup ran out of time; retry with: swe plugins install late@m --harness=codex",
            "Cannot parse JSON in /h/.swe/mcp.json: fix or remove the file",
        ])
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
        // The failed plugin, then the server waiting on its secret once.
        #expect(setup.cloned?.installNotes == [
            "lint@mine isn't installed in \(HarnessMark.accountName("claude")): network is down",
            "github isn't synced yet: no value for GITHUB_TOKEN; set it with: swe mcp secret set GITHUB_TOKEN",
        ])
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
        // A failed run reads the machine again, so the sheet shows what is on disk.
        #expect(runner.calls.last == ["setup", "--json", "--non-interactive"])
    }

    @Test func aRefusedCloneKeepsTheStartYouPicked() async {
        let runner = SetupRunner(preview: hubPreviewJSON)
        runner.cloneRefusal = "can't reach x"
        let setup = model(runner)
        await setup.load()
        setup.start = .seed
        setup.hubURL = "x"
        await setup.cloneHub()
        #expect(setup.start == .seed, "the reload must not switch you back to adopting CLAUDE.md")

        setup.start = .file("/h/.codex/AGENTS.md")
        await setup.cloneHub()
        #expect(setup.start == .file("/h/.codex/AGENTS.md"))
    }

    @Test func aCloneWhosePreviewFailedStillShowsTheClone() async {
        // swe cloned, then its preview stopped (exit 1, document on stdout).
        let runner = SetupRunner(preview: hubPreviewJSON)
        runner.cloned = clonedPreviewJSON
        runner.clonedStatus = 1
        let setup = model(runner)
        await setup.load()
        setup.hubURL = "https://github.com/me/hub.git"
        await setup.cloneHub()
        #expect(setup.hubError == nil)
        #expect(setup.cloned?.url == "https://github.com/me/hub.git")
        #expect(!setup.offersHub, "no second Clone over the hub it just made")
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
