import Foundation
import Testing
@testable import QuiverCore

/// Replies by command: the first rule whose words start the call wins.
/// Records every call, so a test can check what was run and in what order.
final class ScriptedRunner: CommandRunning, @unchecked Sendable {
    var rules: [(prefix: [String], reply: CommandResult)]
    private(set) var calls: [[String]] = []

    init(_ rules: [(prefix: [String], reply: CommandResult)]) { self.rules = rules }

    func run(_ arguments: [String]) async throws -> CommandResult {
        calls.append(arguments)
        for rule in rules where arguments.starts(with: rule.prefix) {
            return rule.reply
        }
        return CommandResult(status: 1, stdout: Data(), stderr: "unscripted: \(arguments.joined(separator: " "))")
    }
}

/// Never answers until cancelled, like a swe stuck behind the login shell.
final class StuckRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    var started: Int { lock.withLock { count } }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock { count += 1 }
        try await Task.sleep(for: .seconds(60))
        return CommandResult(status: 0, stdout: Data(), stderr: "")
    }
}

/// Runs until cancelled, then reports the exit of a terminated child.
final class TerminatedRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var count = 0
    var started: Int { lock.withLock { count } }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock { count += 1 }
        while !Task.isCancelled { try? await Task.sleep(for: .milliseconds(5)) }
        return CommandResult(status: 15, stdout: Data(), stderr: "")
    }
}

/// Takes a moment per call and records the most calls `counts` picks out
/// that were ever running at once.
final class CountingRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private let reply: @Sendable ([String]) -> CommandResult
    private let counts: @Sendable ([String]) -> Bool
    private var inFlight = 0
    private var top = 0
    private var log: [[String]] = []

    init(counts: @escaping @Sendable ([String]) -> Bool,
         reply: @escaping @Sendable ([String]) -> CommandResult) {
        self.counts = counts
        self.reply = reply
    }

    var calls: [[String]] { lock.withLock { log } }
    var peak: Int { lock.withLock { top } }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let counted = counts(arguments)
        lock.withLock {
            log.append(arguments)
            if counted {
                inFlight += 1
                top = max(top, inFlight)
            }
        }
        try await Task.sleep(for: .milliseconds(30))
        if counted { lock.withLock { inFlight -= 1 } }
        return reply(arguments)
    }
}

func ok(_ json: String) -> CommandResult {
    CommandResult(status: 0, stdout: Data(#"{"schema": 1, "data": \#(json)}"#.utf8), stderr: "")
}

let inventoryJSON = #"""
{"shared_dir": "/h/.quiver/skills",
 "harnesses": [
  {"name": "claude", "label": "claude", "path": "/h/.claude/skills", "state": "linked", "detail": ""},
  {"name": "codex", "label": "codex", "path": "/h/.codex/skills", "state": "keep", "detail": "1 of 1 skills exist nowhere else"},
  {"name": "hermes", "label": "hermes", "path": "/h/.hermes/skills", "state": "create", "detail": ""},
  {"name": "droid", "label": "factory", "path": "/h/.factory/skills", "state": "absorb", "detail": "2 skills, all already shared"}
 ],
 "skills": [
  {"name": "alpha", "description": "Does the alpha thing", "path": "/h/.quiver/skills/alpha",
   "root": "shared", "source": "shared", "harnesses": ["claude"]},
  {"name": "only-codex", "description": "", "path": "/h/.codex/skills/only-codex",
   "root": "codex", "source": "harness", "harnesses": ["codex"]},
  {"name": "hammer", "description": "Hits things", "path": "/h/.claude/plugins/cache/kit/toolkit/1.0/skills/hammer",
   "root": "claude-plugin", "source": "plugin", "harnesses": ["claude"]},
  {"name": "future", "path": "/h/x/future", "root": "x", "source": "something-new"}
 ]}
"""#

let hubJSON = #"""
{"hub": "/h/.quiver", "plugins": [
 {"name": "toolkit", "marketplace": "kit", "description": "Tools for testing", "version": "1.2.0",
  "path": "/h/.quiver/plugins/kit/toolkit",
  "components": {"skills": 2, "agents": 0, "commands": 1, "hooks": 0},
  "skills": [{"name": "hammer", "description": "Hits things", "path": "/p/hammer"},
             {"name": "saw", "description": "", "path": "/p/saw"}],
  "install": {"codex": {"state": "not-installed", "fix": "codex plugin add toolkit@kit",
                        "id": "codex:not-installed:toolkit@kit"},
              "claude": {"state": "installed", "fix": null, "id": null}}},
 {"name": "drawer", "marketplace": "kit", "description": "", "version": null,
  "path": "/h/.quiver/plugins/kit/drawer", "components": {}, "skills": [],
  "install": {"claude": {"state": "stale", "fix": "claude plugin update drawer@kit",
                         "id": "claude:stale:drawer@kit"},
              "codex": {"state": "disabled-here", "fix": "set enabled = true in ~/.codex/config.toml",
                        "id": null}}}
]}
"""#

@MainActor
@Suite struct SkillsModelTests {
    func model(_ rules: [(prefix: [String], reply: CommandResult)]) -> (SkillsModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules)
        return (SkillsModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func loadsSkillsAndRootsFromSwe() async throws {
        let (model, runner) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        #expect(runner.calls == [["skills", "--json"]])
        #expect(model.roots.map(\.name) == ["claude", "codex", "hermes", "droid"])
        #expect(model.allSkills.map(\.name) == ["alpha", "only-codex", "hammer", "future"])
        let alpha = try #require(model.allSkills.first)
        #expect(alpha.source == .shared)
        #expect(alpha.description == "Does the alpha thing")
        // An unknown source from a newer swe still loads.
        #expect(model.allSkills[3].source == .harness)
        #expect(model.allSkills[3].harnesses.isEmpty)
    }

    @Test func rootStatesMapToLinkedNotLinkedAndBlocked() {
        #expect(SkillRoot(name: "a", path: "/a", state: "linked").link == .linked)
        for state in ["create", "relink", "absorb"] {
            #expect(SkillRoot(name: "a", path: "/a", state: state).link == .notLinked)
        }
        for state in ["keep", "ignored", "skipped", "conflict"] {
            #expect(SkillRoot(name: "a", path: "/a", state: state).link == .blocked)
        }
    }

    @Test func anArchivedHarnessIsJudgedByWhatItsFolderHolds() {
        // swe reports an archived root's real state, so the app needs no special case.
        #expect(SkillRoot(name: "hermes", path: "/h/.hermes/skills", state: "create", archived: true).link == .notLinked)
        #expect(SkillRoot(name: "codex", path: "/h/.codex/skills", state: "keep",
                          detail: "1 of 1 skills exist nowhere else", archived: true).link == .blocked)
        #expect(SkillRoot(name: "pinokio", path: "/h/.pinokio/skills", state: "ignored",
                          detail: "listed in ~/.quiver/.linkignore").link == .blocked)
    }

    @Test func onlyAFolderOfCopiesNeedsForce() {
        #expect(SkillRoot(name: "a", path: "/a", state: "absorb", detail: "2 skills, all already shared").linkNeedsForce)
        #expect(!SkillRoot(name: "a", path: "/a", state: "absorb", detail: "empty").linkNeedsForce)
        #expect(!SkillRoot(name: "a", path: "/a", state: "create").linkNeedsForce)
        #expect(!SkillRoot(name: "a", path: "/a", state: "keep", detail: "x").linkNeedsForce)
    }

    @Test func linkRunsSweByPathThenReloads() async throws {
        let (model, runner) = model([
            (["skills", "link"], ok(#"{"action": "link", "harness": "hermes", "source": "/h/.hermes/skills", "target": "/h/.quiver/skills"}"#)),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        await model.load()
        let hermes = try #require(model.roots.first { $0.name == "hermes" })
        await model.link(hermes)
        #expect(runner.calls == [
            ["skills", "--json"],
            ["skills", "link", "/h/.hermes/skills", "--json"],
            ["skills", "--json"],
        ])
        #expect(model.actionError == nil)
        #expect(model.busy.isEmpty)
    }

    @Test func linkingAFolderOfCopiesPassesForce() async throws {
        let (model, runner) = model([
            (["skills", "link"], ok(#"{"action": "link"}"#)),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        await model.load()
        let droid = try #require(model.roots.first { $0.name == "droid" })
        await model.link(droid)
        #expect(runner.calls[1] == ["skills", "link", "/h/.factory/skills", "--force", "--json"])
    }

    @Test func blockedAndLinkedFoldersAreNeverLinked() async throws {
        let (model, runner) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        for root in model.roots where root.link != .notLinked {
            await model.link(root)
        }
        // codex keeps a skill nobody else has; claude is already linked.
        #expect(runner.calls == [["skills", "--json"]])
    }

    @Test func unlinkOnlyTouchesALinkedFolder() async throws {
        let (model, runner) = model([
            (["skills", "unlink"], ok(#"{"action": "unlink", "harness": "claude", "path": "/h/.claude/skills", "mkdir": false}"#)),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        await model.load()
        for root in model.roots { await model.unlink(root) }
        #expect(runner.calls == [
            ["skills", "--json"],
            ["skills", "unlink", "/h/.claude/skills", "--json"],
            ["skills", "--json"],
        ])
    }

    @Test func aRefusedLinkShowsSwesReason() async throws {
        let refusal = CommandResult(status: 1, stdout: Data(), stderr: "\u{1B}[31m  /h/.hermes/skills is a non-empty directory.\u{1B}[0m\n")
        let (model, _) = model([
            (["skills", "link"], refusal),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        await model.load()
        await model.link(try #require(model.roots.first { $0.name == "hermes" }))
        #expect(model.actionError == "/h/.hermes/skills is a non-empty directory.")
        #expect(model.busy.isEmpty)
    }

    @Test func theSharedLibraryListsFirst() async {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        #expect(model.visibleSkills.map(\.name) == ["alpha", "only-codex", "future", "hammer"])
    }

    @Test func filtersByHarnessSourceAndText() async {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        model.harness = "claude"
        #expect(model.visibleSkills.map(\.name) == ["alpha", "hammer"])
        model.source = .plugin
        #expect(model.visibleSkills.map(\.name) == ["hammer"])
        model.harness = nil
        model.source = nil
        model.query = "  HITS "
        #expect(model.visibleSkills.map(\.name) == ["hammer"])
    }

    @Test func aSharedSkillShowsEveryFolderItCouldReach() async throws {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        let alpha = try #require(model.allSkills.first { $0.name == "alpha" })
        let chips = model.chips(for: alpha).map { "\($0.root.name):\($0.link)" }
        #expect(chips == ["claude:linked", "codex:blocked", "hermes:notLinked", "droid:notLinked"])
        // A plugin skill belongs to its harness alone; linking cannot reach it.
        let hammer = try #require(model.allSkills.first { $0.name == "hammer" })
        #expect(model.chips(for: hammer).map(\.root.name) == ["claude"])
    }

    @Test func hubListsPluginsWithTheirInstallState() async throws {
        let (model, runner) = model([(["skills", "hub", "--json"], ok(hubJSON))])
        await model.loadHub()
        #expect(runner.calls == [["skills", "hub", "--json"]])
        let plugin = try #require(model.hubPlugins.first)
        #expect(plugin.id == "toolkit@kit")
        #expect(plugin.version == "1.2.0")
        #expect(plugin.components["commands"] == 1)
        #expect(plugin.skills.map(\.name) == ["hammer", "saw"])
        #expect(plugin.harnesses == ["claude", "codex"])
        #expect(plugin.install["codex"]?.state == .notInstalled)
        #expect(plugin.fixes == ["codex plugin add toolkit@kit"])
        #expect(plugin.runnableFixes.map(\.id) == ["codex:not-installed:toolkit@kit"])
        #expect(plugin.manualFixes.isEmpty)
        model.query = "saw"
        #expect(model.hubPlugins.count == 1)
        model.query = "nothing"
        #expect(model.hubPlugins.isEmpty)
    }

    @Test func fixRunsOnlyWhatSweCanRunByIdThenReloads() async throws {
        let (model, runner) = model([
            (["find", "plugins"], ok(#"""
            {"fixed": {"id": "claude:stale:drawer@kit", "harness": "claude", "marketplace": "kit",
             "plugin": "drawer", "ref": "drawer@kit", "kind": "stale", "detail": "1 file differs",
             "fix": "claude plugin update drawer@kit", "runnable": true, "paths": ["SKILL.md"]}}
            """#)),
            (["skills", "hub", "--json"], ok(hubJSON)),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        await model.loadHub()
        let drawer = try #require(model.hubPlugins.first { $0.name == "drawer" })
        // codex's half is an edit by hand: shown, never sent to swe.
        #expect(drawer.manualFixes == ["set enabled = true in ~/.codex/config.toml"])
        await model.fixHub(drawer)
        // The fix can change installed skills too, so both tabs reload.
        #expect(runner.calls == [
            ["skills", "hub", "--json"],
            ["find", "plugins", "--fix=claude:stale:drawer@kit", "--json"],
            ["skills", "--json"],
            ["skills", "hub", "--json"],
        ])
        #expect(model.allSkills.count == 4)
        #expect(model.hubError == nil)
        #expect(model.fixingPlugin == nil)
    }

    @Test func aFailedFixSaysWhyAndStillReloads() async throws {
        let (model, runner) = model([
            (["find", "plugins"], CommandResult(status: 1, stdout: Data(),
                                                stderr: "claude is not on PATH")),
            (["skills", "hub", "--json"], ok(hubJSON)),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        await model.loadHub()
        await model.fixHub(try #require(model.hubPlugins.first { $0.name == "toolkit" }))
        #expect(model.hubError == "claude is not on PATH")
        #expect(runner.calls.suffix(2) == [["skills", "--json"], ["skills", "hub", "--json"]])
    }

    @Test func aPluginWithNothingToRunNeverCallsSwe() async throws {
        let manualOnly = #"""
        {"hub": "/h/.quiver", "plugins": [{"name": "drawer", "marketplace": "kit", "description": "",
          "version": null, "path": "/p", "components": {}, "skills": [],
          "install": {"claude": {"state": "installed", "fix": null, "id": null},
                      "codex": {"state": "disabled-here", "fix": "edit config.toml", "id": null}}}]}
        """#
        let (model, runner) = model([(["skills", "hub", "--json"], ok(manualOnly))])
        await model.loadHub()
        let drawer = try #require(model.hubPlugins.first)
        #expect(drawer.runnableFixes.isEmpty)
        await model.fixHub(drawer)
        #expect(runner.calls.count == 1)
    }

    @Test func anUnknownInstallStateStillDecodes() async throws {
        let json = hubJSON.replacingOccurrences(of: #""state": "installed""#, with: #""state": "quarantined""#)
        let (model, _) = model([(["skills", "hub", "--json"], ok(json))])
        await model.loadHub()
        #expect(model.hubPlugins.first?.install["claude"]?.state == .unknown)
    }

    @Test func aLinkedManifestStateDecodesWithItsOwnTitle() async throws {
        let json = hubJSON.replacingOccurrences(of: #""state": "installed""#, with: #""state": "linked-manifest""#)
        let (model, _) = model([(["skills", "hub", "--json"], ok(json))])
        await model.loadHub()
        let state = model.hubPlugins.first?.install["claude"]?.state
        #expect(state == .linkedManifest)
        #expect(state?.title == "Linked manifest")
    }

    @Test func leavingTheHubMidLoadKeepsItLoadingForTheNextVisit() async {
        let runner = StuckRunner()
        let model = SkillsModel(swe: SweClient(runner: runner), timeout: .milliseconds(200))
        let load = Task { await model.loadHubIfStale(for: "/bin/swe") }
        while runner.started == 0 { await Task.yield() }
        load.cancel()
        #expect(model.hub == .loading)
        // The next visit joins the load rather than starting another.
        await model.loadHubIfStale(for: "/bin/swe")
        #expect(runner.started == 1)
    }

    @Test func aHungSweGivesUpAndSaysSo() async {
        // ProcessRunner answers a cancel by terminating swe and returning its
        // exit status, not by throwing; the timeout still reads as one.
        let runner = TerminatedRunner()
        let model = SkillsModel(swe: SweClient(runner: runner), timeout: .milliseconds(50))
        await model.loadHub()
        guard case .failed(let message) = model.hub else { Issue.record("\(model.hub)"); return }
        #expect(message.contains("didn't answer"))
    }

    @Test func linksRunOneAtATime() async throws {
        let runner = CountingRunner(counts: { $0.contains("link") }, reply: { args in
            args.contains("link") ? ok(#"{"action": "link"}"#) : ok(inventoryJSON)
        })
        let model = SkillsModel(swe: SweClient(runner: runner))
        await model.load()
        let hermes = try #require(model.roots.first { $0.name == "hermes" })
        let droid = try #require(model.roots.first { $0.name == "droid" })
        async let a: Void = model.link(hermes)
        async let b: Void = model.link(droid)
        _ = await (a, b)
        #expect(runner.peak == 1)
        #expect(runner.calls.filter { $0.contains("link") }.count == 2)
    }

    @Test func aFailedLoadSaysWhy() async {
        let (model, _) = model([(["skills", "--json"], CommandResult(status: 1, stdout: Data(), stderr: "boom"))])
        await model.load()
        #expect(model.inventory == .failed("boom"))
    }
}
