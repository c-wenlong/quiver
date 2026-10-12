import Foundation
import Testing
@testable import QuiverCore

@Suite struct McpServerRepoTests {
    let fm = FileManager.default

    private func tempDir() throws -> URL {
        let url = fm.temporaryDirectory.appending(path: "mcp-repo-\(UUID().uuidString)")
        try fm.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    @discardableResult
    private func mkdir(_ path: String, in root: URL, marker: String? = nil) throws -> URL {
        let url = root.appending(path: path)
        try fm.createDirectory(at: url, withIntermediateDirectories: true)
        if let marker { try "".write(to: url.appending(path: marker), atomically: true, encoding: .utf8) }
        return url
    }

    private func git(_ args: String..., in dir: URL) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["-C", dir.path] + args
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        try #require(process.terminationStatus == 0)
    }

    // MARK: - scan

    @Test func groupFoldersHoldTheServers() throws {
        let root = try tempDir()
        try mkdir("official/linear-mcp", in: root, marker: "package.json")
        try mkdir("custom/fathom-mcp", in: root)
        let servers = McpServerRepo.scan(root: root, fileManager: fm)
        #expect(servers.map(\.name) == ["fathom-mcp", "linear-mcp"])
        #expect(servers.map(\.group) == ["custom", "official"])
    }

    @Test func aFolderWithAMarkerIsAServerItself() throws {
        let root = try tempDir()
        for marker in McpServerRepo.serverMarkers {
            let marked = try mkdir("server-\(marker.filter(\.isLetter))", in: root)
            try "".write(to: marked.appending(path: marker), atomically: true, encoding: .utf8)
            try mkdir("\(marked.lastPathComponent)/nested", in: root)
        }
        let servers = McpServerRepo.scan(root: root, fileManager: fm)
        // Every child had a marker, so there are no group folders: the
        // "nested" folders inside them are never reached.
        #expect(servers.count == McpServerRepo.serverMarkers.count)
        #expect(servers.allSatisfy { $0.group == nil })
    }

    @Test func checkoutsAtTheRootHaveNoGroup() throws {
        let root = try tempDir()
        try mkdir("mcp-hn", in: root, marker: ".git")
        try mkdir("grouped/one", in: root)
        let servers = McpServerRepo.scan(root: root, fileManager: fm)
        #expect(servers.map(\.name) == ["mcp-hn", "one"])
        #expect(servers.map(\.group) == [nil, "grouped"])
    }

    @Test func hiddenFoldersAndFilesAreIgnored() throws {
        let root = try tempDir()
        try mkdir(".secret", in: root)
        try mkdir("group/.hidden-server", in: root)
        try mkdir("real", in: root, marker: ".git")
        try "x".write(to: root.appending(path: "notes.txt"), atomically: true, encoding: .utf8)
        try "x".write(to: root.appending(path: "group/file.txt"), atomically: true, encoding: .utf8)
        let servers = McpServerRepo.scan(root: root, fileManager: fm)
        #expect(servers.map(\.name) == ["real"])
    }

    @Test func sortingPutsUngroupedFirstThenGroupsThenNames() throws {
        let root = try tempDir()
        try mkdir("solo", in: root, marker: ".git")
        try mkdir("zebra/zed", in: root)
        try mkdir("apple/bee", in: root)
        try mkdir("apple/Ann", in: root)
        let servers = McpServerRepo.scan(root: root, fileManager: fm)
        #expect(servers.map(\.name) == ["solo", "Ann", "bee", "zed"])
        #expect(servers.map(\.group) == [nil, "apple", "apple", "zebra"])
    }

    @Test func aMissingRootScansAsEmpty() throws {
        let root = fm.temporaryDirectory.appending(path: "no-such-\(UUID().uuidString)")
        #expect(McpServerRepo.scan(root: root, fileManager: fm) == [])
    }

    // MARK: - hubUsage

    private func repo(_ name: String, at path: String) -> McpRepoServer {
        McpRepoServer(name: name, path: URL(fileURLWithPath: path))
    }

    private func hubServer(_ name: String, command: String? = nil, args: [String] = []) -> McpServer {
        McpServer(name: name, command: command, args: args, harnesses: ["claude"])
    }

    @Test func hubUsageMatchesCommandAndArgsInsideTheFolder() throws {
        let servers = [repo("linear-mcp", at: "/srv/mcp/linear-mcp"),
                       repo("fathom-mcp", at: "/srv/mcp/fathom-mcp"),
                       repo("direct", at: "/srv/mcp/direct")]
        let hub = [
            hubServer("linear", command: "node", args: ["/srv/mcp/linear-mcp/build/index.js"]),
            hubServer("fathom", command: "uv", args: ["--directory", "/srv/mcp/fathom-mcp", "run", "fathom-mcp"]),
            hubServer("script", command: "/srv/mcp/direct/run.sh"),
            hubServer("line", command: "node /srv/mcp/linear-mcp/serve.js"),
        ]
        let usage = McpServerRepo.hubUsage(servers: servers, hub: hub)
        #expect(usage["/srv/mcp/linear-mcp"] == ["linear", "line"])
        #expect(usage["/srv/mcp/fathom-mcp"] == ["fathom"])
        #expect(usage["/srv/mcp/direct"] == ["script"])
    }

    @Test func aPrefixFolderNameDoesNotMatch() {
        let servers = [repo("mcp-hn", at: "/srv/mcp-hn"), repo("mcp-hnx", at: "/srv/mcp-hnx")]
        let hub = [hubServer("hn", command: "node", args: ["/srv/mcp-hnx/index.js"])]
        let usage = McpServerRepo.hubUsage(servers: servers, hub: hub)
        #expect(usage["/srv/mcp-hn"] == nil)
        #expect(usage["/srv/mcp-hnx"] == ["hn"])
    }

    @Test func fileURLArgsAndRelativeArgs() {
        let servers = [repo("one", at: "/srv/one")]
        let hub = [
            hubServer("file", command: "open", args: ["file:///srv/one"]),
            hubServer("relative", command: "run", args: ["one", "./one", "srv/one"]),
        ]
        let usage = McpServerRepo.hubUsage(servers: servers, hub: hub)
        #expect(usage["/srv/one"] == ["file"])
    }

    // MARK: - GitState.parse

    @Test func parseReadsTrackingCountsAndChanges() {
        let state = GitState.parse(porcelain: "## main...origin/main [ahead 1, behind 2]\n M src/a.swift\n")
        #expect(state == GitState(branch: "main", ahead: 1, behind: 2, hasChanges: true))
    }

    @Test func parseReadsBranchWithoutUpstream() {
        let state = GitState.parse(porcelain: "## feature\n")
        #expect(state == GitState(branch: "feature"))
    }

    @Test func parseReadsDetachedHead() {
        let state = GitState.parse(porcelain: "## HEAD (no branch)\n")
        #expect(state == GitState(branch: nil))
    }

    @Test func parseReadsARepoWithNoCommits() {
        let state = GitState.parse(porcelain: "## No commits yet on main\n?? new-file\n")
        #expect(state == GitState(branch: "main", hasChanges: true))
    }

    @Test func parseCountsAnyFurtherLineAsAChange() {
        let state = GitState.parse(porcelain: "## main\n?? untracked\n")
        #expect(state.hasChanges)
        #expect(!GitState.parse(porcelain: "## main\n").hasChanges)
    }

    // MARK: - gitState

    @Test func gitStateReadsARealRepo() async throws {
        let dir = try tempDir()
        try git("init", in: dir)
        try git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "x", in: dir)
        var state = await McpServerRepo.gitState(of: dir)
        #expect(state.isRepo)
        #expect(state.branch != nil)
        #expect(!state.hasChanges)
        try "x".write(to: dir.appending(path: "untracked"), atomically: true, encoding: .utf8)
        state = await McpServerRepo.gitState(of: dir)
        #expect(state.isRepo)
        #expect(state.hasChanges)
    }

    @Test func aPlainFolderIsNotARepo() async throws {
        let dir = try tempDir()
        let state = await McpServerRepo.gitState(of: dir)
        #expect(state == GitState(isRepo: false))
    }

    @Test func aFolderInsideAnotherRepoIsStillNotARepo() async throws {
        let dir = try tempDir()
        try git("init", in: dir)
        try git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "x", in: dir)
        let sub = try mkdir("server", in: dir)
        try "x".write(to: sub.appending(path: "notes.txt"), atomically: true, encoding: .utf8)
        // Without its own .git, git status would report the parent repo.
        let state = await McpServerRepo.gitState(of: sub)
        #expect(state == GitState(isRepo: false))
    }

    // MARK: - model

    @Test @MainActor func loadPublishesServersWithHubNamesAndGit() async throws {
        let root = try tempDir()
        let checkout = try mkdir("group/linear-mcp", in: root)
        try git("init", in: checkout)
        let model = McpServerRepoModel(root: root)
        let hub = [McpServer(name: "linear", command: "node",
                             args: [checkout.appending(path: "build/index.js").path],
                             harnesses: ["claude"])]
        await model.load(hub: hub)
        #expect(model.servers.map(\.name) == ["linear-mcp"])
        #expect(model.servers.first?.group == "group")
        #expect(model.servers.first?.hubServers == ["linear"])
        #expect(model.servers.first?.git?.isRepo == true)
        #expect(!model.isLoading)
    }

    // MARK: - CodeEditors

    @Test func installedEditorsKeepTheCandidateOrder() {
        let installed = CodeEditors.installed { url in
            ["Zed.app", "Cursor.app", "Xcode.app"].contains(url.lastPathComponent)
        }
        #expect(installed.map(\.name) == ["Cursor", "Zed", "Xcode"])
        // The system Applications folder wins over the user's.
        #expect(installed.first?.appURL.path == "/Applications/Cursor.app")
    }

    @Test func anEditorOnlyInTheHomeFolderStillCounts() {
        let installed = CodeEditors.installed { url in
            url.lastPathComponent == "Windsurf.app" && url.path.hasPrefix("/Users/")
        }
        #expect(installed.map(\.name) == ["Windsurf"])
        #expect(installed.first?.appURL.path.contains("/Applications/Windsurf.app") == true)
    }
}
