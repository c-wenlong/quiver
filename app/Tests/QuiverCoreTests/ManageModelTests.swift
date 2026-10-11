import Foundation
import Testing
@testable import QuiverCore

/// Answers each swe call by its leading arguments, and records every call.
final class RoutingRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    private var routes: [(prefix: [String], reply: CommandResult)]
    private(set) var calls: [[String]] = []

    init(_ routes: [([String], CommandResult)]) {
        self.routes = routes.map { (prefix: $0.0, reply: $0.1) }
    }

    func set(_ prefix: [String], _ reply: CommandResult) {
        lock.withLock { routes.insert((prefix: prefix, reply: reply), at: 0) }
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        lock.withLock {
            calls.append(arguments)
            let route = routes.first { Array(arguments.prefix($0.prefix.count)) == $0.prefix }
            return route?.reply ?? CommandResult(status: 1, stdout: Data(), stderr: "no route for \(arguments)")
        }
    }
}

func json(_ text: String, status: Int32 = 0) -> CommandResult {
    CommandResult(status: status, stdout: Data(#"{"schema": 1, "data": \#(text)}"#.utf8), stderr: "")
}

// MARK: - AGENTS.md

@MainActor
@Suite struct AgentsModelTests {
    func check(agentsFile: String, claudeState: String = "linked") -> CommandResult {
        json(#"""
        {"agents_file": "\#(agentsFile)", "agents_file_exists": true, "harnesses": [
          {"name": "agents", "instructions": null, "skills": {"path": "/h/.agents/skills", "state": "ignored"}},
          {"name": "claude", "instructions": {"path": "/h/.claude/CLAUDE.md", "state": "\#(claudeState)"},
           "skills": {"path": "/h/.claude/skills", "state": "linked"}},
          {"name": "codex", "instructions": {"path": "/h/.codex/AGENTS.md", "state": "conflict"}, "skills": null}
        ]}
        """#)
    }

    @Test func decodesLinkStatusAndOpensTheSharedFile() async throws {
        let folder = try TempFolder()
        let agents = try folder.write("AGENTS.md", "shared rules\n")
        let runner = RoutingRunner([(["init", "--check", "--json"], check(agentsFile: agents.path))])
        let model = AgentsModel()
        await model.load(swe: SweClient(runner: runner))

        guard case .loaded(let report) = model.check else {
            Issue.record("expected loaded, got \(model.check)")
            return
        }
        #expect(report.instructionRows.map(\.name) == ["claude", "codex"])
        #expect(report.harnesses[1].instructions?.state == .linked)
        #expect(report.harnesses[2].instructions?.state.isBlocked == true)
        #expect(!report.instructionsNeedInit)
        #expect(model.global?.text == "shared rules\n")
    }

    @Test func linkRunsPlainInitThenChecksAgain() async throws {
        let folder = try TempFolder()
        let agents = try folder.write("AGENTS.md", "x")
        let runner = RoutingRunner([
            (["init", "--check", "--json"], check(agentsFile: agents.path, claudeState: "create")),
            (["init"], CommandResult(status: 0, stdout: Data("Quiver layout\n".utf8), stderr: "")),
        ])
        let model = AgentsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        guard case .loaded(let before) = model.check else { Issue.record("not loaded"); return }
        #expect(before.instructionsNeedInit)

        runner.set(["init", "--check", "--json"], check(agentsFile: agents.path))
        await model.link(swe: swe)
        #expect(runner.calls == [["init", "--check", "--json"], ["init"], ["init", "--check", "--json"]])
        #expect(model.linkError == nil)
        guard case .loaded(let after) = model.check else { Issue.record("not loaded"); return }
        #expect(!after.instructionsNeedInit)
    }

    @Test func aFailedInitIsShownAndTheCheckStillRefreshes() async throws {
        let folder = try TempFolder()
        let agents = try folder.write("AGENTS.md", "x")
        let runner = RoutingRunner([
            (["init", "--check", "--json"], check(agentsFile: agents.path)),
            (["init"], CommandResult(status: 1, stdout: Data(), stderr: "Fix or remove ~/.quiver/.linkignore\n")),
        ])
        let model = AgentsModel()
        await model.link(swe: SweClient(runner: runner))
        #expect(model.linkError == "Fix or remove ~/.quiver/.linkignore")
        #expect(runner.calls.last == ["init", "--check", "--json"])
    }

    @Test func reloadingKeepsTheOpenDocumentAndItsEdits() async throws {
        let folder = try TempFolder()
        let agents = try folder.write("AGENTS.md", "x")
        let runner = RoutingRunner([(["init", "--check", "--json"], check(agentsFile: agents.path))])
        let model = AgentsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        model.global?.text = "draft"
        await model.load(swe: swe)
        #expect(model.global?.text == "draft")
        #expect(model.hasUnsavedChanges)
    }

    @Test func projectFilesShowWhatExistsAndWhichOneIsALink() throws {
        let folder = try TempFolder()
        _ = try folder.write("AGENTS.md", "project\n")
        try folder.link("CLAUDE.md", to: "AGENTS.md")
        let model = AgentsModel()
        model.openProject(folder.url)
        #expect(model.projectFiles.map(\.name) == ["AGENTS.md", "CLAUDE.md"])
        #expect(model.projectFiles.map(\.exists) == [true, true])
        #expect(model.projectFiles[0].linksTo == nil)
        #expect(model.projectFiles[1].linksTo == "AGENTS.md")

        // Both names open one document, so two editors can't disagree.
        let agents = model.document(for: model.projectFiles[0])
        let claude = model.document(for: model.projectFiles[1])
        #expect(agents === claude)
        agents.text = "changed"
        #expect(model.projectHasUnsavedChanges)
    }

    @Test func aMissingProjectFileCanBeCreated() throws {
        let folder = try TempFolder()
        let model = AgentsModel()
        model.openProject(folder.url)
        #expect(model.projectFiles.map(\.exists) == [false, false])
        let doc = model.document(for: model.projectFiles[0])
        doc.text = "# Project\n"
        #expect(doc.save() == .saved)
        model.rescanProject()
        #expect(model.projectFiles[0].exists)
    }

    @Test func openingAnotherProjectDropsTheOldDocuments() throws {
        let one = try TempFolder()
        let two = try TempFolder()
        _ = try one.write("AGENTS.md", "one")
        let model = AgentsModel()
        model.openProject(one.url)
        model.document(for: model.projectFiles[0]).text = "edit"
        model.openProject(two.url)
        #expect(!model.projectHasUnsavedChanges)
        #expect(model.projectFolder == two.url)
    }
}

// MARK: - MCP servers

@MainActor
@Suite struct McpModelTests {
    let listing = json(#"""
    {"harnesses": ["claude", "cursor", "codex"], "servers": [
      {"name": "github", "transport": "stdio", "command": "npx", "args": ["gh"], "url": null,
       "env_keys": ["GITHUB_TOKEN"], "header_keys": [], "harnesses": ["claude", "cursor", "codex"],
       "conflict": false, "variants": [["claude", "cursor", "codex"]]},
      {"name": "docs", "transport": "http", "command": null, "args": [], "url": "https://docs.example/mcp",
       "env_keys": [], "header_keys": ["Authorization"], "harnesses": ["claude", "codex"],
       "conflict": true, "variants": [["claude"], ["codex"]]},
      {"name": "solo", "transport": "stdio", "command": "uvx", "args": [], "url": null,
       "env_keys": [], "header_keys": [], "harnesses": ["cursor"], "conflict": false, "variants": [["cursor"]]}
    ]}
    """#)

    func loaded(_ model: McpModel) -> McpInventory? {
        if case .loaded(let inventory) = model.state { return inventory }
        Issue.record("expected loaded, got \(model.state)")
        return nil
    }

    @Test func listsServersAndFiltersDrift() async throws {
        let runner = RoutingRunner([(["mcp", "list", "--json"], listing)])
        let model = McpModel()
        await model.load(swe: SweClient(runner: runner))
        let inventory = try #require(loaded(model))
        #expect(inventory.harnesses == ["claude", "cursor", "codex"])
        #expect(inventory.servers(.all).map(\.name) == ["github", "docs", "solo"])
        #expect(inventory.servers(.drift).map(\.name) == ["docs", "solo"])
        #expect(inventory.servers(.conflicts).map(\.name) == ["docs"])
        let docs = inventory.servers[1]
        #expect(docs.headerKeys == ["Authorization"])
        #expect(docs.group(of: "claude") == 0)
        #expect(docs.group(of: "codex") == 1)
        #expect(docs.group(of: "cursor") == nil)
        #expect(docs.missing(from: inventory.harnesses) == ["cursor"])
        #expect(!model.hasHealth)
    }

    @Test func healthComesFromStatus() async throws {
        let status = json(#"""
        {"harnesses": ["claude"], "servers": [
          {"name": "gh", "transport": "stdio", "command": "nope", "args": [], "url": null, "env_keys": [],
           "header_keys": [], "harnesses": ["claude"], "conflict": false, "variants": [["claude"]],
           "health": "error", "detail": "'nope' not in PATH"}]}
        """#)
        let runner = RoutingRunner([(["mcp", "status", "--json"], status)])
        let model = McpModel()
        await model.load(swe: SweClient(runner: runner), health: true)
        let server = try #require(loaded(model)?.servers.first)
        #expect(server.health == "error")
        #expect(server.detail == "'nope' not in PATH")
        #expect(model.hasHealth)
        #expect(!model.checkingHealth)
    }

    @Test func anOlderSweWithoutHarnessesFallsBackToTheOnesWithServers() throws {
        let body = Data(#"""
        {"servers": [{"name": "a", "transport": "stdio", "command": "x", "args": [], "url": null,
          "env_keys": [], "header_keys": [], "harnesses": ["codex", "claude"], "conflict": true}]}
        """#.utf8)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let inventory = try decoder.decode(McpInventory.self, from: body)
        #expect(inventory.harnesses == ["codex", "claude"])
        // No variants and a conflict: each harness stands alone.
        #expect(inventory.servers[0].groups == [["codex"], ["claude"]])
    }

    @Test func syncTargetsSplitMissingFromDiffering() async throws {
        let runner = RoutingRunner([(["mcp", "list", "--json"], listing)])
        let model = McpModel()
        await model.load(swe: SweClient(runner: runner))
        let inventory = try #require(loaded(model))
        let docs = inventory.servers[1]
        let targets = model.targets(for: docs, from: "claude", in: inventory.harnesses)
        #expect(targets.missing == ["cursor"])
        #expect(targets.differing == ["codex"])
        #expect(!targets.needsOverwrite(["cursor"]))
        #expect(targets.needsOverwrite(["cursor", "codex"]))
        // A harness that already agrees with the source is not a target.
        let github = inventory.servers[0]
        let none = model.targets(for: github, from: "claude", in: inventory.harnesses)
        #expect(none.missing.isEmpty && none.differing.isEmpty)
    }

    @Test func syncBuildsTheSweCommandAndReadsTheReport() async throws {
        let report = json(#"""
        {"source": "claude", "dry_run": true, "selected": ["docs"], "targets": [
          {"name": "cursor", "added": ["docs"], "updated": [], "skipped": [], "pruned": []},
          {"name": "codex", "added": [], "updated": ["docs"], "skipped": [], "pruned": []}]}
        """#)
        let runner = RoutingRunner([(["mcp", "sync"], report)])
        let server = McpServer(name: "docs", harnesses: ["claude", "codex"], conflict: true)
        let result = try await SweClient(runner: runner).mcpSync(
            server: server, from: "claude", to: ["cursor", "codex"], overwrite: true, dryRun: true)
        #expect(runner.calls == [["mcp", "sync", "claude", "cursor", "codex", "--only=docs",
                                  "--no-interactive", "--json", "--force", "--dry-run"]])
        #expect(result.sentence == "Would add to cursor. Would update codex.")
    }

    @Test func aServerNameIsMatchedLiterallyNotAsAGlob() {
        #expect(McpServer(name: "a*b?[c]", harnesses: []).onlyPattern == "a[*]b[?][[]c]")
        #expect(McpServer(name: "dv__github", harnesses: []).onlyPattern == "dv__github")
        #expect(!McpServer(name: "a,b", harnesses: []).canSyncAlone)
    }

    @Test func reportSentences() {
        let done = McpSyncReport(source: "claude", dryRun: false, selected: ["x"], targets: [
            .init(name: "cursor", added: ["x"]), .init(name: "cline", added: ["x"]),
            .init(name: "codex", skipped: ["x"]),
        ])
        #expect(done.sentence == "Added to cursor and cline. Left codex as it was.")
        #expect(McpSyncReport(source: nil, dryRun: false, selected: [], targets: []).sentence == "Nothing to change.")
    }

    @Test func anOlderSweWithoutSyncJSONIsNamedAsSuch() async {
        let runner = RoutingRunner([(["mcp", "sync"], CommandResult(
            status: 1, stdout: Data("Unknown flag(s): --json\n".utf8), stderr: ""))])
        await #expect(throws: SweError.jsonUnsupported) {
            _ = try await SweClient(runner: runner).mcpSync(
                server: McpServer(name: "x", harnesses: []), from: "claude", to: ["cursor"],
                overwrite: false, dryRun: true)
        }
    }
}

// MARK: - Plugins

@MainActor
@Suite struct PluginsModelTests {
    let drift = json(#"""
    {"sources": 3, "drift": [
      {"id": "claude:stale:tool@mk", "harness": "claude", "marketplace": "mk", "plugin": "tool",
       "ref": "tool@mk", "kind": "stale", "detail": "installed 1.0 vs source 1.1, 2 files differ",
       "fix": "claude plugin update tool@mk", "runnable": true, "paths": ["a.md", "b.md"]},
      {"id": "codex:disabled-here:tool@mk", "harness": "codex", "marketplace": "mk", "plugin": "tool",
       "ref": "tool@mk", "kind": "disabled-here", "detail": "disabled in codex, enabled in claude",
       "fix": "set [plugins.\"tool@mk\"] enabled = true in ~/.codex/config.toml", "runnable": false, "paths": []}
    ]}
    """#)

    @Test func decodesFindings() async throws {
        let runner = RoutingRunner([(["find", "plugins", "--json"], drift)])
        let model = PluginsModel()
        await model.load(swe: SweClient(runner: runner))
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        #expect(report.sources == 3)
        #expect(report.drift.map(\.kindTitle) == ["Out of date", "Disabled here"])
        #expect(report.drift[0].paths == ["a.md", "b.md"])
        #expect(!report.drift[1].runnable)
    }

    @Test func fixAsksSweByIdThenReloads() async throws {
        let runner = RoutingRunner([
            (["find", "plugins", "--json"], drift),
            (["find", "plugins", "--fix=claude:stale:tool@mk", "--json"], json(#"""
            {"fixed": {"id": "claude:stale:tool@mk", "harness": "claude", "marketplace": "mk", "plugin": "tool",
             "ref": "tool@mk", "kind": "stale", "detail": "d", "fix": "claude plugin update tool@mk",
             "runnable": true, "paths": []}}
            """#)),
        ])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        await model.fix(report.drift[0], swe: swe)
        #expect(runner.calls == [["find", "plugins", "--json"],
                                 ["find", "plugins", "--fix=claude:stale:tool@mk", "--json"],
                                 ["find", "plugins", "--json"]])
        #expect(model.fixError == nil)
        #expect(model.lastFixed == "claude plugin update tool@mk")
        #expect(model.fixing == nil)
    }

    @Test func anEditFixIsNeverSentToSwe() async throws {
        let runner = RoutingRunner([(["find", "plugins", "--json"], drift)])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        await model.fix(report.drift[1], swe: swe)
        #expect(runner.calls == [["find", "plugins", "--json"]])
    }

    @Test func aFailedFixShowsSwesReason() async throws {
        let runner = RoutingRunner([
            (["find", "plugins", "--json"], drift),
            (["find", "plugins", "--fix=claude:stale:tool@mk"], CommandResult(
                status: 1, stdout: Data(), stderr: "claude plugin update tool@mk failed: offline\n")),
        ])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        await model.fix(report.drift[0], swe: swe)
        #expect(model.fixError == "claude plugin update tool@mk failed: offline")
        #expect(model.lastFixed == nil)
    }

    @Test func anOlderSweSaysToUpdate() async {
        let runner = RoutingRunner([(["find", "plugins", "--json"], CommandResult(
            status: 1, stdout: Data(), stderr: "--json is only available for skills: swe find skills --json\n"))])
        let model = PluginsModel()
        await model.load(swe: SweClient(runner: runner))
        #expect(model.state == .failed(SweError.jsonUnsupported.localizedDescription))
    }
}
