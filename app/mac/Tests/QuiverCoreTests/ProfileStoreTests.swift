import Foundation
import Testing
@testable import QuiverCore

private func root(of home: URL) -> URL {
    home.appendingPathComponent(".quiver/accounts", isDirectory: true)
}

private func makeStore(_ home: URL) -> ProfileStore {
    ProfileStore(root: root(of: home), userHome: home)
}

@Suite struct ProfileStoreTests {
    /// Each test gets its own instance, so its folder goes when the test ends.
    private let home: TempDir
    init() throws { home = try TempDir() }

    /// A scratch directory used as the user's home. The accounts root sits
    /// under it the way it does in production: <home>/.quiver/accounts.
    private func scratch() throws -> URL { home.url }

    @Test func aStoreWithNoFileStillListsTheTwoDefaultsFirst() throws {
        let home = try scratch()
        let profiles = try makeStore(home).load()

        #expect(profiles.map(\.id) == ["claude-default", "codex-default"])
        #expect(profiles.map(\.vendor) == [.claude, .codex])
        #expect(profiles.map(\.name) == ["Default", "Default"])
        #expect(profiles.map(\.isDefault) == [true, true])
        #expect(profiles.map(\.color) == ["#d97757", "#10a37f"])
        #expect(profiles.map(\.createdAt) == [nil, nil])
    }

    @Test func defaultsPointAtTheVendorsOwnHomeAndLeaveTheDesktopDataAlone() throws {
        let home = try scratch()
        let store = makeStore(home)
        let profiles = try store.load()

        let claude = store.paths(for: profiles[0])
        #expect(claude.home.path == home.path + "/.claude")
        #expect(claude.desktopData == nil)

        let codex = store.paths(for: profiles[1])
        #expect(codex.home.path == home.path + "/.codex")
        #expect(codex.desktopData == nil)
    }

    @Test func addedProfilesComeAfterTheDefaultsAndSurviveAReload() throws {
        let home = try scratch()
        let when = Date(timeIntervalSince1970: 1_700_000_000)
        let store = ProfileStore(root: root(of: home), userHome: home, now: { when })

        let work = try store.add(vendor: .claude, name: "  Work Account ")
        #expect(work == Profile(
            id: "claude-work-account", vendor: .claude, name: "Work Account",
            isDefault: false, color: "#3b82f6", createdAt: when))

        let side = try store.add(vendor: .codex, name: "Side")
        #expect(side.id == "codex-side")

        // A fresh store reads what the first one wrote.
        let reloaded = try makeStore(home).load()
        #expect(reloaded.map(\.id) == ["claude-default", "codex-default", "claude-work-account", "codex-side"])
        #expect(reloaded[2] == work)
        #expect(reloaded[3] == side)
    }

    @Test func anAddedProfileGetsPrivateDirectoriesUnderTheAccountsRoot() throws {
        let home = try scratch()
        let store = makeStore(home)
        let work = try store.add(vendor: .claude, name: "Work")

        let base = root(of: home).appendingPathComponent("claude/claude-work", isDirectory: true)
        let paths = store.paths(for: work)
        #expect(paths.home.path == base.path + "/home")
        #expect(paths.desktopData?.path == base.path + "/desktop")

        let everyDirectory = [
            root(of: home), root(of: home).appendingPathComponent("claude"), base,
            paths.home, try #require(paths.desktopData),
        ]
        for dir in everyDirectory {
            #expect(try mode(of: dir) == 0o700, "\(dir.path)")
        }
        #expect(try mode(of: root(of: home).appendingPathComponent("profiles.json")) == 0o600)
    }

    @Test func colorsCycleThroughThePaletteUnlessOneIsGiven() throws {
        let home = try scratch()
        let store = makeStore(home)
        // Two defaults already exist, so the first added profile is the third.
        #expect(try store.add(vendor: .claude, name: "A").color == "#3b82f6")
        #expect(try store.add(vendor: .claude, name: "B").color == "#a855f7")
        #expect(try store.add(vendor: .codex, name: "C", color: "#123abc").color == "#123abc")
    }

    @Test func slugsAreLowercaseDashedAndUnique() throws {
        let home = try scratch()
        let store = makeStore(home)

        #expect(try store.add(vendor: .claude, name: "Work").id == "claude-work")
        #expect(try store.add(vendor: .claude, name: "work").id == "claude-work-2")
        #expect(try store.add(vendor: .claude, name: "WORK!").id == "claude-work-3")
        // The same name under the other vendor is a different id.
        #expect(try store.add(vendor: .codex, name: "Work").id == "codex-work")
        #expect(try store.add(vendor: .codex, name: "  My  Side / Project_1 ").id == "codex-my-side-project-1")
        // The default's own id is taken.
        #expect(try store.add(vendor: .codex, name: "Default").id == "codex-default-2")
        // Nothing usable in the name still gets an id.
        #expect(try store.add(vendor: .claude, name: "日本語").id == "claude-profile")
    }

    @Test func aDirectoryLeftByAnInterruptedRemovalIsNeverAdopted() throws {
        let home = try scratch()
        let store = makeStore(home)
        let leftover = root(of: home).appendingPathComponent("claude/claude-work/home", isDirectory: true)
        try FileManager.default.createDirectory(at: leftover, withIntermediateDirectories: true)

        #expect(try store.add(vendor: .claude, name: "Work").id == "claude-work-2")
    }

    @Test func emptyNamesAreRejectedAndNothingIsWritten() throws {
        let home = try scratch()
        let store = makeStore(home)

        #expect(throws: ProfileStoreError.emptyName) { try store.add(vendor: .claude, name: "") }
        #expect(throws: ProfileStoreError.emptyName) { try store.add(vendor: .codex, name: " \n\t ") }
        #expect(!FileManager.default.fileExists(atPath: root(of: home).path))
    }

    @Test func aMalformedColorIsRejected() throws {
        let home = try scratch()
        let store = makeStore(home)

        #expect(throws: ProfileStoreError.invalidColor("red")) {
            try store.add(vendor: .claude, name: "A", color: "red")
        }
        #expect(throws: ProfileStoreError.invalidColor("#12345")) {
            try store.add(vendor: .claude, name: "A", color: "#12345")
        }
        #expect(try store.add(vendor: .claude, name: "A", color: "#ABC").color == "#ABC")
    }

    @Test func theFileOnDiskIsPlainJSONWithTheDocumentedShape() throws {
        let home = try scratch()
        let when = Date(timeIntervalSince1970: 1_700_000_000)
        let store = ProfileStore(root: root(of: home), userHome: home, now: { when })
        _ = try store.add(vendor: .codex, name: "Side", color: "#ff0000")

        let data = try Data(contentsOf: root(of: home).appendingPathComponent("profiles.json"))
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(json["schema"] as? Int == 1)
        let profiles = try #require(json["profiles"] as? [[String: Any]])
        #expect(profiles.count == 3)
        #expect(profiles[2] as NSDictionary == [
            "id": "codex-side", "vendor": "codex", "name": "Side", "isDefault": false,
            "color": "#ff0000", "createdAt": "2023-11-14T22:13:20Z",
        ] as NSDictionary)
        #expect(profiles[0]["createdAt"] == nil)
    }

    // MARK: - Rename

    @Test func renamingChangesOnlyTheDisplayName() throws {
        let home = try scratch()
        let store = makeStore(home)
        let work = try store.add(vendor: .claude, name: "Work")

        try store.rename(id: work.id, to: "  Day job ")

        let reloaded = try makeStore(home).load()
        let renamed = try #require(reloaded.first { $0.id == "claude-work" })
        #expect(renamed.name == "Day job")
        #expect(renamed.color == work.color)
        #expect(renamed.createdAt == work.createdAt)
        #expect(store.paths(for: renamed) == store.paths(for: work))
        #expect(reloaded.count == 3)
    }

    @Test func aDefaultCanBeRenamedAndKeepsItsPlace() throws {
        let home = try scratch()
        let store = makeStore(home)
        _ = try store.add(vendor: .claude, name: "Work")

        try store.rename(id: "codex-default", to: "Personal")

        let reloaded = try store.load()
        #expect(reloaded.map(\.id) == ["claude-default", "codex-default", "claude-work"])
        #expect(reloaded[1].name == "Personal")
        #expect(reloaded[1].isDefault)
    }

    @Test func renameRejectsEmptyNamesAndUnknownIds() throws {
        let home = try scratch()
        let store = makeStore(home)
        let work = try store.add(vendor: .claude, name: "Work")

        #expect(throws: ProfileStoreError.emptyName) { try store.rename(id: work.id, to: "  ") }
        #expect(throws: ProfileStoreError.notFound("claude-nope")) { try store.rename(id: "claude-nope", to: "X") }
        #expect(try store.load().first { $0.id == work.id }?.name == "Work")
    }

    // MARK: - Remove

    @Test func removingDeletesTheEntryAndEverythingItOwned() throws {
        let home = try scratch()
        let store = makeStore(home)
        let work = try store.add(vendor: .claude, name: "Work")
        let side = try store.add(vendor: .codex, name: "Side")
        // A login the profile accumulated.
        let login = store.paths(for: work).home.appendingPathComponent(".credentials.json")
        try Data("secret".utf8).write(to: login)

        try store.remove(id: work.id)

        #expect(try store.load().map(\.id) == ["claude-default", "codex-default", "codex-side"])
        let vendorDir = root(of: home).appendingPathComponent("claude")
        #expect(!FileManager.default.fileExists(atPath: vendorDir.appendingPathComponent("claude-work").path))
        #expect(FileManager.default.fileExists(atPath: store.paths(for: side).home.path))
        // The id is retired, never free again: its keychain item may still hold the old login.
        #expect(try store.add(vendor: .claude, name: "Work").id == "claude-work-2")
    }

    @Test func defaultsAndUnknownIdsCannotBeRemoved() throws {
        let home = try scratch()
        let store = makeStore(home)
        let work = try store.add(vendor: .claude, name: "Work")
        // A default's home is the vendor's own: plant something to prove it survives.
        let claudeHome = home.appendingPathComponent(".claude", isDirectory: true)
        try FileManager.default.createDirectory(at: claudeHome, withIntermediateDirectories: true)
        let keep = claudeHome.appendingPathComponent("settings.json")
        try Data("{}".utf8).write(to: keep)

        #expect(throws: ProfileStoreError.cannotRemoveDefault) { try store.remove(id: "claude-default") }
        #expect(throws: ProfileStoreError.cannotRemoveDefault) { try store.remove(id: "codex-default") }
        #expect(throws: ProfileStoreError.notFound("claude-nope")) { try store.remove(id: "claude-nope") }

        #expect(FileManager.default.fileExists(atPath: keep.path))
        #expect(try store.load().map(\.id) == ["claude-default", "codex-default", work.id])
    }

    @Test func aProfileWhoseIdEscapesTheRootIsNeverDeleted() throws {
        let home = try scratch()
        let outside = home.appendingPathComponent("precious", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        // Hand-edited file: the id walks out of <root>/claude/.
        _ = try plant("""
        {"schema": 1, "profiles": [
          {"id": "../../../precious", "vendor": "claude", "name": "Evil", "isDefault": false, "color": "#000000"}
        ]}
        """, in: home)
        let store = makeStore(home)

        #expect(performing: { try store.remove(id: "../../../precious") }, throws: isMalformed)
        #expect(performing: { try store.load() }, throws: isMalformed)
        #expect(FileManager.default.fileExists(atPath: outside.path))
    }

    // MARK: - Damaged files

    /// Writes `text` as profiles.json under the accounts root.
    private func plant(_ text: String, in home: URL) throws -> URL {
        let dir = root(of: home)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("profiles.json")
        try Data(text.utf8).write(to: file)
        return file
    }

    private func isMalformed(_ error: any Error) -> Bool {
        if case ProfileStoreError.malformed = error { return true }
        return false
    }

    @Test(arguments: [
        "",
        "not json",
        "{\"schema\": 1}",
        "{\"schema\": 1, \"profiles\": {}}",
        "{\"schema\": 1, \"profiles\": [{\"id\": \"claude-x\"}]}",
        "{\"schema\": 1, \"profiles\": [{\"id\": \"x-y\", \"vendor\": \"gemini\", \"name\": \"Y\", \"isDefault\": false, \"color\": \"#000000\"}]}",
        "{\"schema\": 1, \"profiles\": [{\"id\": \"claude-a\", \"vendor\": \"claude\", \"name\": \"A\", \"isDefault\": false, \"color\": \"#000000\"}, {\"id\": \"claude-a\", \"vendor\": \"claude\", \"name\": \"B\", \"isDefault\": false, \"color\": \"#000000\"}]}",
    ])
    func aMalformedFileThrowsAndIsNeverOverwritten(text: String) throws {
        let home = try scratch()
        let file = try plant(text, in: home)
        let store = makeStore(home)

        #expect(performing: { try store.load() }, throws: isMalformed)
        #expect(performing: { try store.add(vendor: .claude, name: "New") }, throws: isMalformed)
        #expect(performing: { try store.rename(id: "claude-default", to: "X") }, throws: isMalformed)
        #expect(performing: { try store.remove(id: "claude-x") }, throws: isMalformed)

        #expect(try String(contentsOf: file, encoding: .utf8) == text)
        // The lock file is shared by every running copy and may stay behind.
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: root(of: home).path)
            .filter { $0 != "profiles.lock" }
        #expect(leftovers == ["profiles.json"], "no temp files or profile directories")
    }

    @Test func aFileFromANewerQuiverIsRefusedRatherThanRewritten() throws {
        let home = try scratch()
        let text = "{\"schema\": 2, \"profiles\": []}"
        let file = try plant(text, in: home)
        let store = makeStore(home)

        #expect(throws: ProfileStoreError.unsupportedSchema(2)) { try store.load() }
        #expect(throws: ProfileStoreError.unsupportedSchema(2)) { try store.add(vendor: .claude, name: "A") }
        #expect(try String(contentsOf: file, encoding: .utf8) == text)
    }

    @Test func aFileMissingTheDefaultsStillListsThemFirst() throws {
        let home = try scratch()
        _ = try plant("""
        {"schema": 1, "profiles": [
          {"id": "codex-side", "vendor": "codex", "name": "Side", "isDefault": false, "color": "#a855f7"},
          {"id": "codex-default", "vendor": "codex", "name": "Mine", "isDefault": true, "color": "#000000"}
        ]}
        """, in: home)

        let profiles = try makeStore(home).load()
        #expect(profiles.map(\.id) == ["claude-default", "codex-default", "codex-side"])
        // The stored default keeps its edits; the missing one is synthesised.
        #expect(profiles[0].name == "Default")
        #expect(profiles[1].name == "Mine")
        #expect(profiles[1].color == "#000000")
    }

    @Test func aFileThatCannotBeReadIsAnErrorNotAnEmptyStore() throws {
        let home = try scratch()
        // profiles.json is a directory: it exists but cannot be read as a file.
        let file = root(of: home).appendingPathComponent("profiles.json")
        try FileManager.default.createDirectory(at: file, withIntermediateDirectories: true)

        #expect(throws: (any Error).self) { try makeStore(home).load() }
    }
}

private func mode(of url: URL) throws -> Int {
    let attrs = try FileManager.default.attributesOfItem(atPath: url.path)
    return (attrs[.posixPermissions] as? Int ?? -1) & 0o777
}
