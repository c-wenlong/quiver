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
    private let arrivals = CountGate()
    var started: Int { lock.withLock { count } }

    /// True once `n` calls have started, false after `gateTimeout`.
    func arrived(_ n: Int = 1) async -> Bool { await arrivals.reach(n) }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock { count += 1 }
        await arrivals.bump()
        try await Task.sleep(for: .seconds(3600))
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
        // Sleeps until cancelled: a cancelled sleep throws at once.
        try? await Task.sleep(for: .seconds(3600))
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
    private var countedSoFar = 0
    private var log: [[String]] = []
    /// The first counted call parks here until the test opens it, so a
    /// second one that slipped past the model's one-at-a-time rule is in
    /// flight beside it and shows in `peak`, however fast the machine.
    let firstCounted = HoldGate()

    init(counts: @escaping @Sendable ([String]) -> Bool,
         reply: @escaping @Sendable ([String]) -> CommandResult) {
        self.counts = counts
        self.reply = reply
    }

    var calls: [[String]] { lock.withLock { log } }
    var peak: Int { lock.withLock { top } }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let counted = counts(arguments)
        let first = lock.withLock { () -> Bool in
            log.append(arguments)
            guard counted else { return false }
            inFlight += 1
            top = max(top, inFlight)
            countedSoFar += 1
            return countedSoFar == 1
        }
        if first { try await firstCounted.pass() }
        if counted { lock.withLock { inFlight -= 1 } }
        return reply(arguments)
    }
}

/// Answers links and lists, holding the first link at `firstLink` until
/// the test opens it.
private final class FirstLinkHeldRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var log: [[String]] = []
    let firstLink = HoldGate()

    var calls: [[String]] { lock.withLock { log } }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let isLink = arguments.contains("link")
        let first = lock.withLock { () -> Bool in
            log.append(arguments)
            return isLink && !log.dropLast().contains { $0.contains("link") }
        }
        if first { try await firstLink.pass() }
        return isLink ? ok(#"{"action": "link"}"#) : ok(inventoryJSON)
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

@MainActor
@Suite struct SkillsModelTests {
    func model(_ rules: [(prefix: [String], reply: CommandResult)]) -> (SkillsModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules)
        return (SkillsModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func newSkillArgvAndLinkAllSelection() async throws {
        let (model, runner) = model([
            (["skills", "new"], ok(#"{"name": "s", "path": "/h/.quiver/skills/s/SKILL.md"}"#)),
            (["skills", "--json"], ok(inventoryJSON)),
        ])
        let reply = try await model.create(name: "s", description: "Use when: x")
        #expect(reply.path.hasSuffix("SKILL.md"))
        #expect(runner.calls.first ==
            ["skills", "new", "s", "--description=Use when: x", "--json"])
        _ = try await model.create(name: "t", description: "")
        #expect(runner.calls[2] == ["skills", "new", "t", "--json"])

        let roots = [
            SkillRoot(name: "a", path: "/a", state: "create"),
            SkillRoot(name: "b", path: "/b", state: "relink"),
            SkillRoot(name: "c", path: "/c", state: "absorb", detail: "empty"),
            SkillRoot(name: "d", path: "/d", state: "absorb", detail: "2 skills"),
            SkillRoot(name: "e", path: "/e", state: "keep"),
            SkillRoot(name: "f", path: "/f", state: "conflict"),
            SkillRoot(name: "g", path: "/g", state: "create", archived: true),
            SkillRoot(name: "h", path: "/h", state: "linked"),
        ]
        let (link, skipped) = SkillsModel.linkAllTargets(roots)
        #expect(link.map(\.name) == ["a", "b", "c"])
        #expect(skipped.map(\.name).sorted() == ["d", "e", "f"])
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
        // No catalog to read: hammer has no plugin to move to, so it stays.
        model.plugins = .unavailable("swe plugins failed")
        #expect(model.visibleSkills.map(\.name) == ["alpha", "only-codex", "future", "hammer"])
    }

    @Test func nothingIsListedUntilTheCatalogIsKnown() async {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        // Listing now would show hammer, then move it a moment later.
        #expect(model.placement == nil)
        #expect(model.visibleSkills.isEmpty)
        model.plugins = .ready(PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: []))
        // Placed off the main actor: still nothing until it is done.
        #expect(model.placement == nil)
        await model.placed()
        #expect(model.visibleSkills.map(\.name) == ["alpha", "only-codex", "future", "hammer"])
    }

    @Test func aPlacementOvertakenByANewerCatalogIsDropped() async {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        let kit = PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "kit", harnesses: ["claude": .init()], plugins: [
                CatalogPlugin(name: "toolkit", marketplace: "kit", sourcePath: "/h/.claude/plugins/cache/kit/toolkit",
                              harnesses: ["claude": PluginHarnessState(available: true, installed: true)]),
            ]),
        ])
        model.plugins = .ready(kit)
        // swe fails before the first placement lands: its result must not
        // replace the newer one, which keeps every skill here.
        model.plugins = .unavailable("swe plugins failed")
        await model.placed()
        #expect(model.pluginSkillCount == 0)
        #expect(model.visibleSkills.count == 4)
    }

    @Test func aPluginSkillMovesToThePluginThatShipsIt() async throws {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        model.plugins = .ready(PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "kit", harnesses: ["claude": .init()], plugins: [
                CatalogPlugin(name: "toolkit", marketplace: "kit", harnesses: [
                    "claude": PluginHarnessState(available: true, installed: true, enabled: true,
                                                 path: "/h/.claude/plugins/cache/kit/toolkit/1.0"),
                ]),
            ]),
        ]))
        await model.load()
        #expect(model.visibleSkills.map(\.name) == ["alpha", "only-codex", "future"])
        #expect(model.pluginSkillCount == 1)
        #expect(model.placement?.byPlugin["toolkit@kit"]?.map(\.name) == ["hammer"])
    }

    @Test func aSourceFilterLeftWithNoSkillsIsCleared() async {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        model.plugins = .unavailable("swe plugins failed")
        await model.load()
        model.source = .plugin
        // The catalog arrives and takes hammer, the only plugin skill.
        model.plugins = .ready(PluginCatalog(harnesses: ["claude"], actions: ["claude"], marketplaces: [
            PluginMarketplace(name: "kit", harnesses: ["claude": .init()], plugins: [
                CatalogPlugin(name: "toolkit", marketplace: "kit", sourcePath: "/h/.claude/plugins/cache/kit/toolkit",
                              harnesses: ["claude": PluginHarnessState(available: true, installed: true)]),
            ]),
        ]))
        await model.placed()
        #expect(model.source == nil)
        #expect(model.visibleSkills.count == 3)
    }

    @Test func filtersByHarnessSourceAndText() async {
        let (model, _) = model([(["skills", "--json"], ok(inventoryJSON))])
        await model.load()
        model.plugins = .unavailable("swe plugins failed")
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

    @Test func leavingThePageMidLoadKeepsItLoadingForTheNextVisit() async {
        let runner = StuckRunner()
        let model = SkillsModel(swe: SweClient(runner: runner), timeout: .milliseconds(200))
        let load = Task { await model.loadIfStale(for: "/bin/swe") }
        #expect(await runner.arrived())
        load.cancel()
        #expect(model.inventory == .loading)
        // The next visit joins the load rather than starting another.
        await model.loadIfStale(for: "/bin/swe")
        #expect(runner.started == 1)
    }

    @Test func aHungSweGivesUpAndSaysSo() async {
        // ProcessRunner answers a cancel by terminating swe and returning its
        // exit status, not by throwing; the timeout still reads as one.
        let runner = TerminatedRunner()
        let model = SkillsModel(swe: SweClient(runner: runner), timeout: .milliseconds(50))
        await model.load()
        guard case .failed(let message) = model.inventory else { Issue.record("\(model.inventory)"); return }
        #expect(message.contains("didn't answer"))
    }

    @Test func linksRunOneAtATime() async throws {
        let runner = FirstLinkHeldRunner()
        let model = SkillsModel(swe: SweClient(runner: runner))
        await model.load()
        let hermes = try #require(model.roots.first { $0.name == "hermes" })
        let droid = try #require(model.roots.first { $0.name == "droid" })
        let a = Task { await model.link(hermes) }
        #expect(await runner.firstLink.arrived())
        let b = Task { await model.link(droid) }
        // The second link has asked (its row is busy) while the first is
        // still held inside swe.
        #expect(await observe(until: { model.busy.count == 2 }))
        runner.firstLink.open()
        await a.value
        await b.value
        // One at a time: the first link's reload lands before the second
        // link starts, never two links back to back.
        let tail = runner.calls.dropFirst().map { $0.contains("link") ? "link" : "list" }
        #expect(tail == ["link", "list", "link", "list"])
    }

    @Test func aFailedLoadSaysWhy() async {
        let (model, _) = model([(["skills", "--json"], CommandResult(status: 1, stdout: Data(), stderr: "boom"))])
        await model.load()
        #expect(model.inventory == .failed("boom"))
    }
}
