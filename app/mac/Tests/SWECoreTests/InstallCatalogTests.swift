import Foundation
import Testing
@testable import SWECore

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
     "command": "brew install claude-code", "available": false}],
  "uninstall": {"via": "cask", "package": "claude-code@latest",
   "command": "brew uninstall --cask claude-code@latest", "available": true}},
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
        // Logos: a cask is Homebrew too; a script has none.
        #expect(all[0].uninstall?.logo == "homebrew")
        #expect(all[1].methods.map(\.logo) == ["npm", "nix"])
        #expect(all[2].methods[0].logo == nil)
        #expect(["homebrew", "npm", "uv", "nix"].allSatisfy { HarnessIcons.icon(for: $0) != nil })
        #expect(all[0].path == "/opt/homebrew/bin/claude")
        #expect(all[0].uninstall?.via == "cask")
        #expect(all[0].uninstall?.managerTitle == "Homebrew cask")
        #expect(all[1].uninstall == nil)
    }

    @Test func decodesTheOlderShapeWithDefaults() async throws {
        let (model, _) = model([(["install"], ok(oldInstallJSON))])
        await model.load()
        guard case .loaded(let all) = model.state else {
            Issue.record("expected loaded, got \(model.state)"); return
        }
        #expect(all[0].registered == false)
        #expect(all[0].description == "")
        #expect(all[0].uninstall == nil)
        // website and app arrived after this shape; they decode as nil.
        #expect(all[0].website == nil)
        #expect(all[0].app == nil)
    }

    /// `website` is the vendor download page the sheet links out to; `app`
    /// is a found `.app` bundle. Both are nullable and must survive decode.
    @Test func decodesWebsiteAndApp() async throws {
        let (model, _) = model([(["install"], ok(#"""
        {"harnesses": [
         {"name": "zcode", "command": "zcode", "installed": true,
          "path": "/Applications/ZCode.app", "app": "/Applications/ZCode.app",
          "website": "https://zcode.z.ai", "registered": false,
          "methods": [{"via": "cask", "package": "zcode",
           "command": "brew install --cask zcode", "available": true}]}
        ]}
        """#))])
        await model.load()
        let zcode = try #require(model.harness("zcode"))
        #expect(zcode.website == "https://zcode.z.ai")
        #expect(zcode.app == "/Applications/ZCode.app")
        #expect(zcode.path == zcode.app)
        #expect(model.harness("gemini") == nil)
    }

    @Test func looksUpOneHarnessByName() async throws {
        let (model, _) = model([(["install"], ok(installJSON))])
        #expect(model.harness("gemini") == nil)        // nothing until loaded
        await model.load()
        #expect(model.harness("gemini")?.methods.count == 2)
        #expect(model.harness("antigravity") == nil)   // no installer on file
    }

    @Test func registerRunsSweAddThenReloads() async throws {
        let (model, runner) = model([
            (["add"], CommandResult(status: 0, stdout: Data(#"{"schema": 1, "data": {"name": "jules", "command": "jules", "action": "added", "installed": true, "aliases": [], "tags": ["agentic", "coding"]}}"#.utf8), stderr: "")),
            (["install"], ok(installJSON)),
        ])
        await model.load()
        let jules = try #require(model.harness("jules"))
        let failure = await model.register(jules)
        #expect(failure == nil)
        #expect(runner.calls[0] == ["install", "--json"])
        #expect(runner.calls[1] == ["add", "jules", "jules", "--no-replace", "--json"])
        #expect(runner.calls[2] == ["install", "--json"])
    }

    @Test func registerPassesTheDescriptionAsAFlag() async throws {
        let runner = ScriptedRunner([
            (["add"], CommandResult(status: 0, stdout: Data(#"{"schema": 1, "data": {"name": "gemini", "command": "gemini", "action": "added", "installed": true, "aliases": [], "tags": ["agentic", "coding"]}}"#.utf8), stderr: "")),
            (["install"], ok(#"""
            {"harnesses":[{"name":"gemini","command":"gemini",
             "installed":true,"path":"/usr/bin/gemini","description":"Google's Gemini CLI",
             "registered":false,"methods":[]}]}
            """#)),
        ])
        let model = InstallCatalogModel(swe: SweClient(runner: runner))
        await model.load()
        _ = await model.register(try #require(model.harness("gemini")))
        #expect(runner.calls[1] == ["add", "gemini", "gemini", "--description=Google's Gemini CLI",
                                    "--no-replace", "--json"])
    }

    @Test func registerSurfacesTheErrorInline() async throws {
        let (model, _) = model([
            (["add"], CommandResult(status: 1, stdout: Data(), stderr: "Usage: swe add")),
            (["install"], ok(installJSON)),
        ])
        await model.load()
        let failure = await model.register(try #require(model.harness("jules")))
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

    /// Built from the package, each token quoted, for every method swe
    /// reports an uninstall for; a script has none.
    @Test func uninstallLinePerMethod() {
        func line(_ via: String, _ package: String) -> String? {
            InstallCatalogModel.uninstallLine(.init(via: via, package: package, command: "", available: true))
        }
        #expect(line("cask", "claude-code@latest") == "'brew' 'uninstall' '--cask' 'claude-code@latest'")
        #expect(line("brew", "gemini-cli") == "'brew' 'uninstall' 'gemini-cli'")
        #expect(line("npm", "@openai/codex") == "'npm' 'uninstall' '-g' '@openai/codex'")
        #expect(line("uv", "aider-chat") == "'uv' 'tool' 'uninstall' 'aider-chat'")
        #expect(line("nix", "codex") == "'nix' 'profile' 'remove' 'codex'")
        #expect(line("script", "https://example.com/install.sh") == nil)
    }

    /// Two concurrent registrations share the registry gate with the page's
    /// edits, so their `swe add` calls run one after another, never at once.
    @Test func registrationsRunThroughTheRegistryGate() async throws {
        let runner = OrderedRunner()
        let model = InstallCatalogModel(swe: SweClient(runner: runner))
        await model.load()
        let first = Task { await model.register(.init(name: "one", command: "one", installed: true)) }
        #expect(await runner.firstAdd.arrived())
        let second = Task { await model.register(.init(name: "two", command: "two", installed: true)) }
        // The second registration has asked while the first is still
        // held inside swe.
        #expect(await observe(until: { model.registering.count == 2 }))
        runner.firstAdd.open()
        _ = await (first.value, second.value)
        let adds = runner.events.filter { $0 != "list" }
        #expect(adds == ["start:one", "end:one", "start:two", "end:two"])
    }

    /// A load cancelled while a second load is already in flight must not
    /// roll the generation back — rewinding it made the second run's
    /// `run == generation` check fail and left the sheet at .loading
    /// forever. Both calls park at their own gate: the cancel interrupts
    /// call one, and the test opens call two once the cancel has landed.
    @Test func aCancelledFirstLoadKeepsTheInFlightResult() async throws {
        let empty = ok(#"{"harnesses": []}"#)
        let runner = GatedReplies([empty, empty])
        let model = InstallCatalogModel(swe: SweClient(runner: runner))
        let first = Task { await model.load() }
        #expect(await runner.gates[0].arrived())
        async let second: Void = model.load()
        #expect(await runner.gates[1].arrived())
        first.cancel()
        await first.value
        runner.gates[1].open()
        await second
        guard case .loaded = model.state else {
            Issue.record("expected loaded, got \(model.state)")
            return
        }
    }

    /// Records when each `swe add` starts and ends, so a test can see the
    /// calls never overlap, and holds the first add at `firstAdd` until the
    /// test opens it; other commands answer an empty catalog.
    private final class OrderedRunner: CommandRunning, @unchecked Sendable {
        private let lock = NSLock()
        private var log: [String] = []
        let firstAdd = HoldGate()
        var events: [String] { lock.withLock { log } }

        func run(_ arguments: [String]) async throws -> CommandResult {
            guard arguments.first == "add" else {
                lock.withLock { log.append("list") }
                return ok(#"{"harnesses":[]}"#)
            }
            let isFirst = lock.withLock { () -> Bool in
                defer { log.append("start:\(arguments[1])") }
                return !log.contains { $0.hasPrefix("start:") }
            }
            if isFirst { try await firstAdd.pass() }
            lock.withLock { log.append("end:\(arguments[1])") }
            return CommandResult(status: 0, stdout: Data(), stderr: "")
        }
    }
}
