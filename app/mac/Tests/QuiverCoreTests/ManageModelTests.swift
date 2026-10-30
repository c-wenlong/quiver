import Foundation
import Testing
@testable import QuiverCore

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
        let folder = try TempDir()
        let agents = try folder.write("AGENTS.md", "shared rules\n")
        let runner = ScriptedRunner([(["init", "--check", "--json"], check(agentsFile: agents.path))])
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
        let folder = try TempDir()
        let agents = try folder.write("AGENTS.md", "x")
        let runner = ScriptedRunner([
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

    @Test func replaceRunsInitWithForce() async throws {
        let folder = try TempDir()
        let agents = try folder.write("AGENTS.md", "x")
        let runner = ScriptedRunner([
            (["init", "--check", "--json"], check(agentsFile: agents.path)),
            (["init", "--force"], CommandResult(status: 0, stdout: Data(), stderr: "")),
        ])
        let model = AgentsModel()
        await model.link(swe: SweClient(runner: runner), force: true)
        #expect(runner.calls == [["init", "--force"], ["init", "--check", "--json"]])
        #expect(model.linkError == nil)
    }

    @Test func replaceWaitsForUnsavedProjectEdits() async throws {
        let folder = try TempDir()
        _ = try folder.write("AGENTS.md", "project\n")
        let runner = ScriptedRunner([])
        let model = AgentsModel()
        model.openProject(folder.url)
        let doc = model.document(for: model.projectFiles[0])
        await model.settled()
        doc.text = "changed"
        await model.link(swe: SweClient(runner: runner), force: true)
        #expect(runner.calls.isEmpty)
        #expect(model.linkError == AgentsModel.saveFirst)
    }

    @Test func aFailedInitIsShownAndTheCheckStillRefreshes() async throws {
        let folder = try TempDir()
        let agents = try folder.write("AGENTS.md", "x")
        let runner = ScriptedRunner([
            (["init", "--check", "--json"], check(agentsFile: agents.path)),
            (["init"], CommandResult(status: 1, stdout: Data(), stderr: "Fix or remove ~/.quiver/.linkignore\n")),
        ])
        let model = AgentsModel()
        await model.link(swe: SweClient(runner: runner))
        #expect(model.linkError == "Fix or remove ~/.quiver/.linkignore")
        #expect(runner.calls.last == ["init", "--check", "--json"])
    }

    @Test func aFileThatInitCreatesShowsUpInTheEditor() async throws {
        let folder = try TempDir()
        let agents = folder.url.appending(path: "AGENTS.md")
        let runner = ScriptedRunner([(["init", "--check", "--json"], check(agentsFile: agents.path))])
        let model = AgentsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        #expect(model.global?.existsOnDisk == false)
        // swe init seeds the file
        _ = try folder.write("AGENTS.md", "# Seeded\n")
        runner.set(["init"], CommandResult(status: 0, stdout: Data(), stderr: ""))
        await model.link(swe: swe)
        #expect(model.global?.text == "# Seeded\n")
        #expect(model.global?.existsOnDisk == true)
    }

    @Test func reloadingKeepsTheOpenDocumentAndItsEdits() async throws {
        let folder = try TempDir()
        let agents = try folder.write("AGENTS.md", "x")
        let runner = ScriptedRunner([(["init", "--check", "--json"], check(agentsFile: agents.path))])
        let model = AgentsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        model.global?.text = "draft"
        await model.load(swe: swe)
        #expect(model.global?.text == "draft")
        #expect(model.hasUnsavedChanges)
    }

    @Test func projectFilesShowWhatExistsAndWhichOneIsALink() async throws {
        let folder = try TempDir()
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
        await model.settled()
        agents.text = "changed"
        #expect(model.projectHasUnsavedChanges)
    }

    @Test func aDocumentAlwaysWritesTheFileItIsCachedUnder() async throws {
        let folder = try TempDir()
        _ = try folder.write("AGENTS.md", "agents\n")
        _ = try folder.write("OTHER.md", "other\n")
        try folder.link("CLAUDE.md", to: "AGENTS.md")
        let model = AgentsModel()
        model.openProject(folder.url)
        _ = model.document(for: model.projectFiles[1])   // CLAUDE.md first
        await model.settled()

        // The link now points elsewhere; AGENTS.md must still edit AGENTS.md.
        try FileManager.default.removeItem(at: folder.url.appending(path: "CLAUDE.md"))
        try folder.link("CLAUDE.md", to: "OTHER.md")
        model.rescanProject()
        let agents = model.document(for: model.projectFiles[0])
        let other = model.document(for: model.projectFiles[1])
        await model.settled()
        #expect(agents.writeURL.lastPathComponent == "AGENTS.md")
        #expect(agents.text == "agents\n")
        #expect(other.text == "other\n")
    }

    @Test func checkDiskRereadsOpenProjectFilesAndRescans() async throws {
        let folder = try TempDir()
        _ = try folder.write("AGENTS.md", "v1\n")
        let model = AgentsModel()
        model.openProject(folder.url)
        let doc = model.document(for: model.projectFiles[0])
        await model.settled()
        _ = try folder.write("AGENTS.md", "v2\n")
        _ = try folder.write("CLAUDE.md", "new\n")
        await model.checkDisk()
        #expect(doc.text == "v2\n")
        #expect(model.projectFiles[1].exists)
    }

    @Test func aMissingProjectFileCanBeCreated() async throws {
        let folder = try TempDir()
        let model = AgentsModel()
        model.openProject(folder.url)
        #expect(model.projectFiles.map(\.exists) == [false, false])
        let doc = model.document(for: model.projectFiles[0])
        await model.settled()
        doc.text = "# Project\n"
        #expect(await doc.save() == .saved)
        model.rescanProject()
        #expect(model.projectFiles[0].exists)
    }

    @Test func openingAnotherProjectDropsTheOldDocuments() async throws {
        let one = try TempDir()
        let two = try TempDir()
        _ = try one.write("AGENTS.md", "one")
        let model = AgentsModel()
        model.openProject(one.url)
        let doc = model.document(for: model.projectFiles[0])
        await model.settled()
        doc.text = "edit"
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
        let runner = ScriptedRunner([(["mcp", "list", "--json"], listing)])
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

    @Test func aHarnessWithNoServersGetsNoColumnAndCausesNoDrift() throws {
        let inventory = McpInventory(harnesses: ["claude", "kilo", "codex"], servers: [
            McpServer(name: "gh", harnesses: ["claude", "codex"]),
            McpServer(name: "solo", harnesses: ["codex"]),
        ])
        #expect(inventory.activeHarnesses == ["claude", "codex"])
        #expect(inventory.idleHarnesses == ["kilo"])
        #expect(inventory.servers(.drift).map(\.name) == ["solo"])
    }

    @Test func driftAndConflictsAreJudgedAcrossTheComparedHarnesses() {
        let inventory = McpInventory(harnesses: ["claude", "desktop", "codex"], servers: [
            McpServer(name: "gh", harnesses: ["claude", "codex"]),
            McpServer(name: "docs", harnesses: ["claude", "desktop", "codex"], conflict: true,
                      variants: [["claude", "codex"], ["desktop"]]),
        ])
        #expect(inventory.servers(.drift).map(\.name) == ["gh", "docs"])
        #expect(inventory.servers(.drift, across: ["claude", "codex"]).isEmpty)
        #expect(inventory.servers(.conflicts, across: ["claude", "codex"]).isEmpty)
        #expect(inventory.servers(.conflicts, across: ["claude", "desktop"]).map(\.name) == ["docs"])
    }

    @Test func healthComesFromStatus() async throws {
        let status = json(#"""
        {"harnesses": ["claude"], "servers": [
          {"name": "gh", "transport": "stdio", "command": "nope", "args": [], "url": null, "env_keys": [],
           "header_keys": [], "harnesses": ["claude"], "conflict": false, "variants": [["claude"]],
           "health": "error", "detail": "'nope' not in PATH"}]}
        """#)
        let runner = ScriptedRunner([(["mcp", "status", "--json"], status)])
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
        let runner = ScriptedRunner([(["mcp", "list", "--json"], listing)])
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
        let runner = ScriptedRunner([(["mcp", "sync"], report)])
        let server = McpServer(name: "docs", harnesses: ["claude", "codex"], conflict: true)
        let result = try await SweClient(runner: runner).mcpSync(
            server: server, from: "claude", to: ["cursor", "codex"], overwrite: true, dryRun: true)
        #expect(runner.calls == [["mcp", "sync", "claude", "cursor", "codex", "--only=docs",
                                  "--no-interactive", "--json", "--force", "--dry-run"]])
        #expect(result.sentence == "Would add to cursor. Would update codex.")
    }

    @Test func onlyApprovedReplacementsRunWithForce() async throws {
        let report = { (target: String, key: String) in json(#"""
            {"source": "claude", "dry_run": false, "selected": ["docs"], "targets": [
              {"name": "\#(target)", "added": \#(key == "added" ? #"["docs"]"# : "[]"), "updated": \#(key == "updated" ? #"["docs"]"# : "[]"), "skipped": [], "pruned": []}]}
            """#) }
        let runner = ScriptedRunner([
            (["mcp", "sync", "claude", "cursor"], report("cursor", "added")),
            (["mcp", "sync", "claude", "codex"], report("codex", "updated")),
        ])
        let server = McpServer(name: "docs", harnesses: ["claude", "codex"], conflict: true,
                               variants: [["claude"], ["codex"]])
        let model = McpModel()
        let result = try await model.sync(server, from: "claude", adding: ["cursor"], replacing: ["codex"],
                                          dryRun: false, swe: SweClient(runner: runner))
        #expect(runner.calls == [
            ["mcp", "sync", "claude", "cursor", "--only=docs", "--no-interactive", "--json"],
            ["mcp", "sync", "claude", "codex", "--only=docs", "--no-interactive", "--json", "--force"],
        ])
        #expect(result.targets.map(\.name) == ["cursor", "codex"])
        #expect(result.sentence == "Added to cursor. Updated codex.")
    }

    @Test func aFailedReplaceStillReportsWhatWasAdded() async throws {
        let runner = ScriptedRunner([
            (["mcp", "sync", "claude", "cursor"], json(#"""
            {"source": "claude", "dry_run": false, "selected": ["docs"], "targets": [
              {"name": "cursor", "added": ["docs"], "updated": [], "skipped": [], "pruned": []}]}
            """#)),
            (["mcp", "sync", "claude", "codex"], CommandResult(status: 1, stdout: Data(), stderr: "codex config is corrupt\n")),
        ])
        let server = McpServer(name: "docs", harnesses: ["claude", "codex"], conflict: true)
        do {
            _ = try await McpModel().sync(server, from: "claude", adding: ["cursor"], replacing: ["codex"],
                                          dryRun: false, swe: SweClient(runner: runner))
            Issue.record("expected a partial failure")
        } catch let partial as McpPartialSync {
            #expect(partial.report.targets.map(\.name) == ["cursor"])
            #expect(partial.message == "codex config is corrupt")
        }
    }

    @Test func anEmptyNameCanNotBeSyncedAlone() {
        // `--only=` with nothing after it would select every server.
        #expect(!McpServer(name: "", harnesses: []).canSyncAlone)
    }

    @Test func aSyncWithNothingToReplaceNeverForces() async throws {
        let runner = ScriptedRunner([(["mcp", "sync"], json(#"{"source": "claude", "dry_run": true, "selected": [], "targets": []}"#))])
        _ = try await McpModel().sync(McpServer(name: "x", harnesses: ["claude"]), from: "claude",
                                      adding: ["cursor"], replacing: [], dryRun: true,
                                      swe: SweClient(runner: runner))
        #expect(runner.calls.count == 1)
        #expect(!runner.calls[0].contains("--force"))
    }

    @Test func serverCanBeLookedUpInTheCurrentInventory() async throws {
        let runner = ScriptedRunner([(["mcp", "list", "--json"], listing)])
        let model = McpModel()
        await model.load(swe: SweClient(runner: runner))
        #expect(model.server(named: "docs")?.harnesses == ["claude", "codex"])
        #expect(model.server(named: "nope") == nil)
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
        let runner = ScriptedRunner([(["mcp", "sync"], CommandResult(
            status: 1, stdout: Data("Unknown flag(s): --json\n".utf8), stderr: ""))])
        await #expect(throws: SweError.jsonUnsupported) {
            _ = try await SweClient(runner: runner).mcpSync(
                server: McpServer(name: "x", harnesses: []), from: "claude", to: ["cursor"],
                overwrite: false, dryRun: true)
        }
    }
}

/// Synthetic secrets only: SECRET marks what must never be shown.
@Suite struct McpSummaryTests {
    func stdio(_ command: String?, _ args: [String]) -> McpServer {
        McpServer(name: "s", command: command, args: args, harnesses: ["claude"])
    }

    func http(_ url: String) -> McpServer {
        McpServer(name: "s", transport: "http", url: url, harnesses: ["claude"])
    }

    @Test func urlQueryValuesUserinfoAndFragmentAreHidden() {
        let server = http("https://me:SECRETpass@mcp.example.com/v1/mcp?api_key=SECRET1&region=us&flag#token=SECRET2")
        #expect(server.summary == "https://…@mcp.example.com/v1/mcp?api_key=…&region=…&…#…")
    }

    @Test func aKeyInTheURLPathIsHidden() {
        #expect(http("https://actions.example.com/mcp/sk-ak-SECRET0123456789abcdef/sse").summary
                == "https://actions.example.com/mcp/…/sse")
    }

    @Test func anOrdinaryURLIsUnchanged() {
        #expect(http("https://docs.example/mcp").summary == "https://docs.example/mcp")
        #expect(http("http://localhost:8080/mcp/sse").summary == "http://localhost:8080/mcp/sse")
    }

    @Test func secretFlagsHideTheirValue() {
        let server = stdio("npx", [
            "-y", "server", "--token", "SECRET1", "--api-key=SECRET2", "--key", "SECRET3", "--secret", "SECRET4",
            "--password", "SECRET5", "--access-token", "SECRET6", "--header", "Authorization: Bearer SECRET7",
            "-H", "X-Api-Key:SECRET8", "--port", "8080",
        ])
        #expect(server.summary == "npx -y server --token … --api-key=… --key … --secret … --password … "
                + "--access-token … --header Authorization:… -H X-Api-Key:… --port 8080")
    }

    @Test func assignmentArgsHideTheirValue() {
        #expect(stdio("env", ["API_KEY=SECRET1", "GITHUB_TOKEN=SECRET2", "node", "server.js"]).summary
                == "env API_KEY=… GITHUB_TOKEN=… node server.js")
    }

    @Test func aURLInsideTheArgsIsRedacted() {
        #expect(stdio("npx", ["mcp-remote", "https://mcp.example.com/sse?token=SECRET1"]).summary
                == "npx mcp-remote https://mcp.example.com/sse?token=…")
    }

    @Test func aWholeCommandLineInTheCommandIsRedacted() {
        #expect(stdio("npx server --token SECRET1 --api-key=SECRET2", []).summary
                == "npx server --token … --api-key=…")
    }

    @Test func aPositionalKeyIsHidden() {
        #expect(stdio("uvx", ["server", "sk-proj-SECRET0123456789abcd"]).summary == "uvx server …")
    }

    @Test func aKeyWithNoDigitsIsHidden() {
        #expect(stdio("uvx", ["server", "ghp_SECRETabcdefghijklmnopqrstuvwxyz", "sk-abcdefghijklmnopqrstu"]).summary
                == "uvx server … …")
        #expect(http("https://actions.example.com/mcp/SECRETabcdefghijKLMNOP/sse").summary
                == "https://actions.example.com/mcp/…/sse")
    }

    @Test func aQuotedValueInTheCommandStaysOneWord() {
        let server = stdio(#"npx server --header "Authorization: Bearer SECRET1" --password 'two SECRET2 words' "#
                           + #"--token="SECRET3 x" API_KEY="SECRET4 y" --name "My Server""#, [])
        #expect(server.summary == #"npx server --header "Authorization:… --password … --token=… API_KEY=… "#
                + #"--name "My Server""#)
    }

    @Test func aCommandInsideQuotesOrOneArgIsRedactedToo() {
        #expect(stdio("bash -c 'server --token SECRET1 --name x'", []).summary == "bash -c 'server --token … --name x'")
        #expect(stdio("bash", ["-c", "server --api-key SECRET2 KEY=SECRET3"]).summary
                == "bash -c server --api-key … KEY=…")
        #expect(stdio(#"sh -c "bash -c 'x --password SECRET4'""#, []).summary == #"sh -c "bash -c 'x --password …'""#)
        #expect(stdio(#"npx mcp-remote "https://x.example.com/sse?token=SECRET5""#, []).summary
                == #"npx mcp-remote "https://x.example.com/sse?token=…""#)
        #expect(stdio("sh", [#"-c"#, #"x"--token SECRET6""#]).summary.contains("SECRET") == false)
        #expect(stdio(#"npx "--token SECRET7"#, []).summary.contains("SECRET") == false)
    }

    @Test func aURLWithASpaceIsNeverSplitOpen() {
        #expect(stdio(#"npx mcp-remote "https://host.example/mcp?token=two SECRET1""#, []).summary
                == #"npx mcp-remote "https://host.example/mcp?token=… …""#)
        #expect(stdio("npx", ["mcp-remote", "https://host.example/a b --token SECRET2"]).summary
                == "npx mcp-remote https://host.example/a …")
        #expect(stdio("bash -c 'mcp-remote \"https://host.example/?k=a SECRET3\"'", []).summary
                == #"bash -c 'mcp-remote "https://host.example/?k=… …"'"#)
    }

    @Test func aQuotedOrEscapedFlagNameStillHidesItsValue() {
        #expect(stdio(#"npx --"token" SECRET1 --\token SECRET2 "--key" SECRET3 --'api-key'=SECRET4"#, []).summary
                == #"npx --"token" … --\token … "--key" … --'api-key'=…"#)
        #expect(stdio("npx", [#"--"password""#, "SECRET5", #""-H""#, "Authorization: Bearer SECRET6"]).summary
                == #"npx --"password" … "-H" Authorization:…"#)
    }

    @Test func aPercentEncodedKeyInTheURLPathIsHidden() {
        #expect(http("https://h.example/mcp/sk%2Dproj%2DSECRET0123456789abcd/sse").summary == "https://h.example/mcp/…/sse")
        #expect(http("https://h.example/My%20Docs/mcp").summary == "https://h.example/My%20Docs/mcp")
    }

    @Test func aFlagAndItsValueInOneArgAreSplit() {
        #expect(stdio("npx", ["--token SECRET1", "--api-key=SECRET2 two", "--header Authorization: Bearer SECRET3"]).summary
                == "npx --token … --api-key=… --header Authorization:…")
    }

    @Test func anOrdinaryCommandIsUnchanged() {
        let server = stdio("uvx", ["mcp-server-fetch", "--port", "8080", "@modelcontextprotocol/server-filesystem",
                                    "/Users/me/My Files", "chrome-devtools-mcp@latest"])
        #expect(server.summary == "uvx mcp-server-fetch --port 8080 @modelcontextprotocol/server-filesystem "
                + "/Users/me/My Files chrome-devtools-mcp@latest")
    }

    @Test func noSummaryEverShowsASecret() {
        let servers = [
            http("https://me:SECRETpass@mcp.example.com/v1/mcp?api_key=SECRET1#SECRET2"),
            stdio("npx --token SECRET3", ["--api-key", "SECRET4", "KEY=SECRET5", "--password=SECRET6"]),
        ]
        for server in servers { #expect(!server.summary.contains("SECRET"), "\(server.summary)") }
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
        let runner = ScriptedRunner([(["find", "plugins", "--json"], drift)])
        let model = PluginsModel()
        await model.load(swe: SweClient(runner: runner))
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        #expect(report.sources == 3)
        #expect(report.drift.map(\.kindTitle) == ["Out of date", "Disabled here"])
        #expect(report.drift[0].paths == ["a.md", "b.md"])
        #expect(!report.drift[1].runnable)
    }

    @Test func aLinkedManifestReadsInSentenceCase() async throws {
        let linked = json(#"""
        {"sources": 1, "drift": [
          {"id": "codex:linked-manifest:tool@mk", "harness": "codex", "marketplace": "mk", "plugin": "tool",
           "ref": "tool@mk", "kind": "linked-manifest",
           "detail": ".codex-plugin is a symlink to .claude-plugin; codex doesn't follow symlinks, so it installs a generated manifest without the version",
           "fix": "cp -R /p/tool/.claude-plugin /p/tool/.codex-plugin.quiver-tmp && rm /p/tool/.codex-plugin && mv /p/tool/.codex-plugin.quiver-tmp /p/tool/.codex-plugin",
           "runnable": true, "paths": []}
        ]}
        """#)
        let model = PluginsModel()
        await model.load(swe: SweClient(runner: ScriptedRunner([(["find", "plugins", "--json"], linked)])))
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        #expect(report.drift.map(\.kindTitle) == ["Linked manifest"])
        #expect(report.drift[0].runnable)
        #expect(!report.drift[0].isSevere)
    }

    @Test func fixAsksSweByIdThenReloads() async throws {
        let runner = ScriptedRunner([
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
        let runner = ScriptedRunner([(["find", "plugins", "--json"], drift)])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        await model.fix(report.drift[1], swe: swe)
        #expect(runner.calls == [["find", "plugins", "--json"]])
    }

    @Test func aFailedFixShowsSwesReason() async throws {
        let runner = ScriptedRunner([
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

    // MARK: Fix all

    @Test func fixAllRunsEachRunnableFindingInTurnAndSkipsEdits() async throws {
        let runner = DriftRunner(["claude:stale:a@mk": true, "codex:stale:b@mk": true,
                                  "codex:disabled-here:a@mk": false])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        #expect(model.fixAllCount == 2)
        await model.fixAll(confirmed: model.fixAllIDs, swe: swe)
        #expect(runner.fixed == ["claude:stale:a@mk", "codex:stale:b@mk"])
        #expect(model.fixError == nil)
        #expect(model.lastFixed == "2 fixes")
        #expect(model.fixing == nil)
        #expect(model.fixAllProgress == nil)
        // Only the edit is left, and Fix all has nothing more to run.
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        #expect(report.drift.map(\.id) == ["codex:disabled-here:a@mk"])
        #expect(model.fixAllCount == 0)
    }

    @Test func fixAllSkipsAFindingAnEarlierFixAlreadyCleared() async throws {
        let runner = DriftRunner(["codex:linked-manifest:a@mk": true, "codex:stale:a@mk": true],
                                 clears: ["codex:linked-manifest:a@mk": ["codex:stale:a@mk"]])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        await model.fixAll(confirmed: model.fixAllIDs, swe: swe)
        #expect(runner.fixed == ["codex:linked-manifest:a@mk"])
        #expect(model.fixError == nil)
        #expect(model.lastFixed == "1 fix")
    }

    @Test func fixAllRunsOnlyWhatTheUserConfirmed() async throws {
        // Registering a marketplace exposes "not installed" findings the
        // confirmation never listed; those wait for their own confirmation.
        let runner = DriftRunner(["claude:unregistered:mk": true],
                                 reveals: ["claude:unregistered:mk": ["claude:not-installed:a@mk"]])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        await model.fixAll(confirmed: model.fixAllIDs, swe: swe)
        #expect(runner.fixed == ["claude:unregistered:mk"])
        guard case .loaded(let report) = model.state else { Issue.record("\(model.state)"); return }
        #expect(report.drift.map(\.id) == ["claude:not-installed:a@mk"])
        #expect(model.fixAllCount == 1)
    }

    @Test func fixAllStopsWhenAnotherSweIsPickedMidBatch() async throws {
        let model = PluginsModel()
        // The other swe sees the same machine, so it still lists b.
        let other = SweClient(runner: DriftRunner(["codex:stale:b@mk": true]))
        let runner = DriftRunner(["claude:stale:a@mk": true, "codex:stale:b@mk": true])
        // Settings switches to another swe while the first fix runs.
        runner.onFix = { _ in await model.loadIfStale(swe: other, for: "/other/swe") }
        let swe = SweClient(runner: runner)
        await model.load(swe: swe, for: "/this/swe")
        await model.fixAll(confirmed: model.fixAllIDs, swe: swe, for: "/this/swe")
        #expect(runner.fixed == ["claude:stale:a@mk"])
        #expect(model.fixAllProgress == nil)
        #expect(model.fixing == nil)
        // The batch stops rather than fix b through a swe no longer chosen.
        #expect(model.fixError == "Stopped: another swe was picked in Settings.")
    }

    @Test func fixAllStopsAtTheFirstFailureAndSaysWhy() async throws {
        let runner = DriftRunner(["claude:stale:a@mk": true, "codex:stale:b@mk": true],
                                 failing: ["claude:stale:a@mk": "claude plugin update a@mk failed: offline"])
        let model = PluginsModel()
        let swe = SweClient(runner: runner)
        await model.load(swe: swe)
        await model.fixAll(confirmed: model.fixAllIDs, swe: swe)
        #expect(runner.fixed.isEmpty)
        #expect(runner.attempted == ["claude:stale:a@mk"])
        #expect(model.fixError == "claude plugin update a@mk failed: offline")
        #expect(model.lastFixed == nil)
        #expect(model.fixAllProgress == nil)
    }

    @Test func anOlderSweSaysToUpdate() async {
        let runner = ScriptedRunner([(["find", "plugins", "--json"], CommandResult(
            status: 1, stdout: Data(), stderr: "--json is only available for skills: swe find skills --json\n"))])
        let model = PluginsModel()
        await model.load(swe: SweClient(runner: runner))
        #expect(model.state == .failed(SweError.jsonUnsupported.localizedDescription))
    }
}

/// swe's plugin drift as state: each fix removes its finding, may clear
/// others, or fails with a reason.
final class DriftRunner: CommandRunning, @unchecked Sendable {
    private let lock = NSLock()
    /// Finding id -> runnable, in report order.
    private var findings: [(id: String, runnable: Bool)]
    private let clears: [String: [String]]
    private let reveals: [String: [String]]
    private let failing: [String: String]
    private(set) var fixed: [String] = []
    private(set) var attempted: [String] = []
    /// Runs after a fix succeeds, outside the lock.
    var onFix: (@Sendable (String) async -> Void)?

    init(_ findings: KeyValuePairs<String, Bool>, clears: [String: [String]] = [:],
         reveals: [String: [String]] = [:], failing: [String: String] = [:]) {
        self.findings = findings.map { (id: $0.key, runnable: $0.value) }
        self.clears = clears
        self.reveals = reveals
        self.failing = failing
    }

    func run(_ arguments: [String]) async throws -> CommandResult {
        let result = answer(arguments)
        if let fix = arguments.first(where: { $0.hasPrefix("--fix=") }), result.status == 0 {
            await onFix?(String(fix.dropFirst("--fix=".count)))
        }
        return result
    }

    private func answer(_ arguments: [String]) -> CommandResult {
        lock.withLock {
            if let fix = arguments.first(where: { $0.hasPrefix("--fix=") }) {
                let id = String(fix.dropFirst("--fix=".count))
                attempted.append(id)
                if let reason = failing[id] {
                    return CommandResult(status: 1, stdout: Data(), stderr: reason + "\n")
                }
                let gone = Set([id] + (clears[id] ?? []))
                findings.removeAll { gone.contains($0.id) }
                findings += (reveals[id] ?? []).map { (id: $0, runnable: true) }
                fixed.append(id)
                return json(#"{"fixed": \#(entry(id, runnable: true))}"#)
            }
            let rows = findings.map { entry($0.id, runnable: $0.runnable) }.joined(separator: ",")
            return json(#"{"sources": 2, "drift": [\#(rows)]}"#)
        }
    }

    private func entry(_ id: String, runnable: Bool) -> String {
        let parts = id.split(separator: ":").map(String.init)
        let ref = parts[2]
        let plugin = ref.contains("@") ? #""\#(ref.split(separator: "@")[0])""# : "null"
        return #"{"id": "\#(id)", "harness": "\#(parts[0])", "marketplace": "mk", "plugin": \#(plugin), "ref": "\#(ref)", "kind": "\#(parts[1])", "detail": "d", "fix": "fix \#(id)", "runnable": \#(runnable), "paths": []}"#
    }
}
