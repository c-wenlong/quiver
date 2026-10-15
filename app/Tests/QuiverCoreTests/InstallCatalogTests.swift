import Foundation
import Testing
@testable import QuiverCore

/// The real `swe install --json` shape: schema wrapper, per-harness
/// description + registered, and the methods list.
private let installJSON = #"""
{"harnesses": [
 {"name": "claude", "command": "claude", "installed": true,
  "path": "/opt/homebrew/bin/claude", "description": "Anthropic's agentic CLI",
  "registered": true,
  "methods": [
    {"via": "npm", "package": "@anthropic-ai/claude-code",
     "command": "npm install -g @anthropic-ai/claude-code", "available": true},
    {"via": "brew", "package": "claude-code",
     "command": "brew install claude-code", "available": false}]},
 {"name": "gemini", "command": "gemini", "installed": false, "path": null,
  "description": "Google's Gemini CLI", "registered": false,
  "methods": [
    {"via": "npm", "package": "@google/gemini-cli",
     "command": "npm install -g @google/gemini-cli", "available": true},
    {"via": "nix", "package": "nixpkgs#gemini-cli",
     "command": "nix profile install nixpkgs#gemini-cli", "available": true}]},
 {"name": "jules", "command": "jules", "installed": true,
  "path": "/usr/local/bin/jules", "description": "", "registered": false,
  "methods": [
    {"via": "script", "package": "curl script",
     "command": "curl -fsSL https://jules.example.com/install.sh | sh",
     "available": true}]}
]}
"""#

/// What a swe from before description/registered answers: those keys and the
/// whole methods edge cases must degrade, not fail.
private let oldInstallJSON = #"""
{"harnesses": [
 {"name": "gemini", "command": "gemini", "installed": false, "path": null,
  "methods": [{"via": "npm", "package": "@google/gemini-cli",
   "command": "npm install -g @google/gemini-cli", "available": true}]}
]}
"""#

@MainActor
@Suite struct InstallCatalogTests {
    private func model(_ rules: [(prefix: [String], reply: CommandResult)])
        -> (InstallCatalogModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules)
        return (InstallCatalogModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func decodesTheRealShape() async throws {
        let (model, runner) = model([(["install"], ok(installJSON))])
        await model.load()
        #expect(runner.calls == [["install", "--json"]])
        guard case .loaded(let all) = model.state else {
            Issue.record("expected loaded, got \(model.state)"); return
        }
        #expect(all.count == 3)
        #expect(all[0].registered == true)
        #expect(all[0].description == "Anthropic's agentic CLI")
        #expect(all[1].methods.map(\.via) == ["npm", "nix"])
        #expect(all[1].methods[0].managerTitle == "npm")
        #expect(all[1].methods[1].managerTitle == "Nix")
        #expect(all[0].methods[1].managerTitle == "Homebrew")
        #expect(all[2].methods[0].managerTitle == "Script")
        #expect(all[0].path == "/opt/homebrew/bin/claude")
    }

    @Test func decodesTheOlderShapeWithDefaults() async throws {
        let (model, _) = model([(["install"], ok(oldInstallJSON))])
        await model.load()
        guard case .loaded(let all) = model.state else {
            Issue.record("expected loaded, got \(model.state)"); return
        }
        #expect(all[0].registered == false)
        #expect(all[0].description == "")
    }

    @Test func hidesRegisteredAndGroupsInstalledFirst() async throws {
        let (model, _) = model([(["install"], ok(installJSON))])
        await model.load()
        let groups = model.groups
        #expect(groups.installed.map(\.name) == ["jules"])
        #expect(groups.missing.map(\.name) == ["gemini"])
    }

    @Test func queryMatchesNameAndDescription() async throws {
        let (model, _) = model([(["install"], ok(installJSON))])
        await model.load()
        model.query = "gem"
        #expect(model.rows.map(\.name) == ["gemini"])
        model.query = "google"          // only in the description
        #expect(model.rows.map(\.name) == ["gemini"])
        model.query = "claude"          // registered stays hidden even on a match
        #expect(model.rows.isEmpty)
    }

    /// An older swe has no `registered` key; the page's harness list still
    /// tells the model which names are taken, so claude stays hidden.
    @Test func registeredNamesHideWhatAnOlderSweCannotFlag() async throws {
        let runner = ScriptedRunner([(["install"], ok(#"""
            {"harnesses":[{"name":"claude","command":"claude","installed":true,
             "path":"/usr/local/bin/claude",
             "methods":[{"via":"npm","package":"@anthropic-ai/claude-code",
              "command":"npm install -g @anthropic-ai/claude-code","available":true}]}]}
            """#))])
        let model = InstallCatalogModel(swe: SweClient(runner: runner),
                                        registeredNames: { ["claude"] })
        await model.load()
        #expect(model.rows.isEmpty)
        #expect(model.everythingRegistered)
    }

    @Test func registerRunsSweAddThenReloads() async throws {
        let (model, runner) = model([
            (["add"], CommandResult(status: 0, stdout: Data("  ✓ Added 'jules'".utf8), stderr: "")),
            (["install"], ok(installJSON)),
        ])
        await model.load()
        let jules = model.rows.first { $0.name == "jules" }!
        let failure = await model.register(jules)
        #expect(failure == nil)
        #expect(runner.calls[0] == ["install", "--json"])
        #expect(runner.calls[1] == ["add", "jules", "jules"])
        #expect(runner.calls[2] == ["install", "--json"])
    }

    @Test func registerPassesTheDescriptionAsArgv() async throws {
        let runner = ScriptedRunner([
            (["add"], CommandResult(status: 0, stdout: Data(), stderr: "")),
            (["install"], ok(#"""
            {"harnesses":[{"name":"gemini","command":"gemini",
             "installed":true,"path":"/usr/bin/gemini","description":"Google's Gemini CLI",
             "registered":false,"methods":[]}]}
            """#)),
        ])
        let model = InstallCatalogModel(swe: SweClient(runner: runner))
        await model.load()
        _ = await model.register(model.rows[0])
        #expect(runner.calls[1] == ["add", "gemini", "gemini", "Google's Gemini CLI"])
    }

    @Test func registerSurfacesTheErrorInline() async throws {
        let (model, _) = model([
            (["add"], CommandResult(status: 1, stdout: Data(), stderr: "Usage: swe add")),
            (["install"], ok(installJSON)),
        ])
        await model.load()
        let failure = await model.register(model.rows.first { $0.name == "jules" }!)
        #expect(failure != nil)
        #expect(model.actionError != nil)
    }

    /// The line is typed into a login shell as-is, so every token is quoted
    /// on its own rather than the whole line as one word.
    @Test func installLineQuotesEveryToken() {
        #expect(InstallCatalogModel.installLine("gemini", via: "npm", swe: "swe")
            == "'swe' 'install' 'gemini' '--via=npm'")
    }

    @Test func installLineQuotesACustomSwePath() {
        let line = InstallCatalogModel.installLine("gemini", via: "nix",
                                                 swe: "/opt/my tools/swe")
        #expect(line == "'/opt/my tools/swe' 'install' 'gemini' '--via=nix'")
    }

    /// Two concurrent registrations share the registry gate with the page's
    /// edits, so their `swe add` calls run one after another, never at once.
    @Test func registrationsRunThroughTheRegistryGate() async throws {
        let runner = OrderedRunner()
        let model = InstallCatalogModel(swe: SweClient(runner: runner))
        await model.load()
        async let first = model.register(.init(name: "one", command: "one", installed: true))
        async let second = model.register(.init(name: "two", command: "two", installed: true))
        _ = await (first, second)
        let adds = runner.events.filter { $0 != "list" }
        #expect(adds == ["start:one", "end:one", "start:two", "end:two"]
                || adds == ["start:two", "end:two", "start:one", "end:one"])
    }

    /// Records when each `swe add` starts and ends, so a test can see the
    /// calls never overlap; other commands answer an empty catalog.
    private final class OrderedRunner: CommandRunning, @unchecked Sendable {
        private let lock = NSLock()
        private(set) var events: [String] = []

        func run(_ arguments: [String]) async throws -> CommandResult {
            guard arguments.first == "add" else {
                lock.withLock { events.append("list") }
                return ok(#"{"harnesses":[]}"#)
            }
            lock.withLock { events.append("start:\(arguments[1])") }
            try? await Task.sleep(for: .milliseconds(50))
            lock.withLock { events.append("end:\(arguments[1])") }
            return CommandResult(status: 0, stdout: Data(), stderr: "")
        }
    }
}
