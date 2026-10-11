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
  "install": {"codex": {"state": "not-installed", "fix": "codex plugin install toolkit@kit"},
              "claude": {"state": "installed", "fix": null}}}
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
        #expect(plugin.fixes == ["codex plugin install toolkit@kit"])
        model.query = "saw"
        #expect(model.hubPlugins.count == 1)
        model.query = "nothing"
        #expect(model.hubPlugins.isEmpty)
    }

    @Test func aFailedLoadSaysWhy() async {
        let (model, _) = model([(["skills", "--json"], CommandResult(status: 1, stdout: Data(), stderr: "boom"))])
        await model.load()
        #expect(model.inventory == .failed("boom"))
    }
}
