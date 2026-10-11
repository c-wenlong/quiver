import Foundation
import Testing
@testable import QuiverCore

/// Synthetic keys only: nothing here is a real credential.
let sampleKeysFile = """
# Keys for every harness
# ANTHROPIC
export ANTHROPIC_API_KEY="sk-ant-test-0000000000000000abcd"

# OPENAI
export OPENAI_API_KEY='sk-proj-test-111111111111wxyz'
export OPENAI_ORG=org-test
alias ll='ls -l'

# GEMINI
EXPORT GEMINI_API_KEY="AIza-test"
"""

/// A retired key left commented out above its replacement, as editors do.
/// Synthetic values only.
let commentedKeysFile = """
# MiMo (Xiaomi)
# export MIMO_API_KEY="sk-old-000000000000000000000000dead"
export MIMO_API_KEY="sk-new-000000000000000000000000beef"
"""

@Suite struct APIKeysCommentedOutTests {
    @Test func aCommentedOutExportIsNotAHeading() {
        let file = APIKeysFile(text: commentedKeysFile)
        #expect(file.entries.map(\.provider) == ["MiMo (Xiaomi)"])
        #expect(file.entries.map(\.name) == ["MIMO_API_KEY"])
    }

    @Test func aCommentedOutBareAssignmentIsNotAHeading() {
        let file = APIKeysFile(text: "# DeepSeek\n#DEEPSEEK_API_KEY=sk-old-1111\nexport DEEPSEEK_API_KEY=sk-new-2222\n")
        #expect(file.entries.map(\.provider) == ["DeepSeek"])
    }

    @Test func aCommentedOutKeyWithNoHeadingLeavesTheProviderEmpty() {
        let file = APIKeysFile(text: "# export OLD_KEY=sk-old-3333\nexport NEW_KEY=sk-new-4444\n")
        #expect(file.entries.map(\.provider) == [""])
    }

    @Test func noProviderEverHoldsAnAssignment() {
        let texts = [commentedKeysFile, sampleKeysFile,
                     "#  export   A_KEY = x\n# B_KEY=y # note\n#export C_KEY='z'\nexport D_KEY=w\n"]
        for text in texts {
            for entry in APIKeysFile(text: text).entries {
                #expect(!entry.provider.contains("="), "provider \(entry.provider) holds an assignment")
            }
        }
    }

    @Test func aCommentedOutKeyIsReportedWithItsValueHidden() {
        let file = APIKeysFile(text: commentedKeysFile)
        #expect(file.problems == [.init(line: 2, text: "# export MIMO_API_KEY=…",
                                        reason: "a commented-out key still holds a value; delete the line")])
        #expect(!file.problems.contains { $0.text.contains("sk-old") })
    }

    @Test func removingTheLastKeyKeepsACommentedOutKeyLine() throws {
        var file = APIKeysFile(text: commentedKeysFile)
        try file.remove(name: "MIMO_API_KEY")
        #expect(file.text.contains("# export MIMO_API_KEY="))
        #expect(!file.text.contains("sk-new"))
    }
}

@Suite struct APIKeysFileTests {
    @Test func entriesAreGroupedUnderTheCommentAboveThem() throws {
        let file = APIKeysFile(text: sampleKeysFile)
        #expect(file.entries.map(\.name) == ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_ORG"])
        #expect(file.entries.map(\.provider) == ["ANTHROPIC", "OPENAI", "OPENAI"])
        #expect(file.entries.map(\.value) == ["sk-ant-test-0000000000000000abcd", "sk-proj-test-111111111111wxyz", "org-test"])
    }

    @Test func linesItCannotUseAreReportedNotDropped() {
        let file = APIKeysFile(text: sampleKeysFile)
        #expect(file.problems == [.init(line: 11, text: "EXPORT GEMINI_API_KEY=…",
                                        reason: "zsh only accepts lowercase export, so this line sets nothing")])
        #expect(file.text == sampleKeysFile)
    }

    @Test func editingAValueRewritesOnlyThatLine() throws {
        var file = APIKeysFile(text: sampleKeysFile)
        try file.set(name: "OPENAI_ORG", value: "org-new", provider: "OPENAI")
        let lines = file.text.components(separatedBy: "\n")
        let before = sampleKeysFile.components(separatedBy: "\n")
        #expect(lines.count == before.count)
        #expect(lines[6] == "export OPENAI_ORG=\"org-new\"")
        #expect(Array(lines[..<6]) == Array(before[..<6]))
        #expect(Array(lines[7...]) == Array(before[7...]))
    }

    @Test func aNewKeyJoinsItsProviderOrStartsOne() throws {
        var file = APIKeysFile(text: sampleKeysFile)
        try file.set(name: "OPENAI_PROJECT", value: "p", provider: "OPENAI")
        try file.set(name: "MISTRAL_API_KEY", value: "m", provider: "MISTRAL")
        let lines = file.text.components(separatedBy: "\n")
        #expect(lines[7] == "export OPENAI_PROJECT=\"p\"")
        #expect(lines.suffix(3) == ["", "# MISTRAL", "export MISTRAL_API_KEY=\"m\""])
        #expect(APIKeysFile(text: file.text).entries.map(\.name).contains("MISTRAL_API_KEY"))
    }

    @Test func valuesAreQuotedSoTheShellReadsThemBack() throws {
        var file = APIKeysFile(text: "")
        try file.set(name: "TRICKY", value: #"a"b$c`d\e"#, provider: "TEST")
        #expect(file.text.contains(#"export TRICKY="a\"b\$c\`d\\e""#))
        #expect(APIKeysFile(text: file.text).entries.first?.value == #"a"b$c`d\e"#)
    }

    @Test func badNamesAndMultilineValuesAreRefused() {
        var file = APIKeysFile(text: "")
        #expect(throws: APIKeysError.invalidName("1BAD")) { try file.set(name: "1BAD", value: "x", provider: "P") }
        #expect(throws: APIKeysError.multilineValue) { try file.set(name: "OK", value: "a\nb", provider: "P") }
    }

    @Test func warningLinesNeverShowTheirValue() {
        let file = APIKeysFile(text: "  EXPORT API_KEY=\"secret-value\"\nexport B=\"x\"; rm -rf /tmp/nothing\n")
        #expect(file.problems.map(\.text) == ["  EXPORT API_KEY=…", "export B=…"])
        #expect(!file.problems.contains { $0.text.contains("secret-value") })
    }

    @Test func extraShellTextAfterAValueMakesTheLineReadOnly() {
        let file = APIKeysFile(text: """
            export A="one"; export B="two"
            export C="prefix""suffix"
            export D="fine"   # a trailing comment is fine
            export E=bare more
            """)
        #expect(file.entries.map(\.name) == ["D"])
        #expect(file.entries.first?.value == "fine")
        #expect(file.problems.map(\.line) == [1, 2, 4])
    }

    @Test func aKeyExportedTwiceIsFlaggedAndNotEdited() {
        var file = APIKeysFile(text: "export A=\"old\"\nexport A=\"current\"\n")
        #expect(file.problems.map(\.line) == [1])
        #expect(file.problems.first?.reason == "A is exported again on line 2, which wins; remove one of them")
        // The row is the one the shell ends up with.
        #expect(file.keys.map(\.value) == ["current"])
        #expect(throws: APIKeysError.duplicate("A")) { try file.set(name: "A", value: "new", provider: "P") }
        #expect(throws: APIKeysError.duplicate("A")) { try file.remove(name: "A") }
    }

    @Test func aProviderCannotAddShellLines() {
        var file = APIKeysFile(text: "")
        #expect(throws: APIKeysError.invalidProvider) {
            try file.set(name: "A", value: "1", provider: "Vendor\nexport OTHER=unexpected")
        }
    }

    @Test func removingTheLastKeyOfAProviderRemovesItsHeading() throws {
        var file = APIKeysFile(text: sampleKeysFile)
        try file.remove(name: "ANTHROPIC_API_KEY")
        #expect(!file.text.contains("ANTHROPIC"))
        #expect(file.text.hasPrefix("# Keys for every harness\n# OPENAI\n") || file.text.hasPrefix("# Keys for every harness\n\n# OPENAI\n"))
        try file.remove(name: "OPENAI_ORG")
        #expect(file.text.contains("# OPENAI"))
    }
}

@Suite struct APIKeyMaskTests {
    @Test func aKeyShowsItsPrefixAndLastFour() {
        #expect(APIKeyMask.mask("sk-ant-api03-abcdefghijklmnop3f9a") == "sk-ant-••••••••3f9a")
        #expect(APIKeyMask.mask("sk-proj-0123456789012345a21c") == "sk-proj-••••••••a21c")
    }

    @Test func aShortValueIsFullyHidden() {
        #expect(APIKeyMask.mask("org-test") == "••••••••")
        #expect(APIKeyMask.mask("") == "")
    }

    @Test func anUnprefixedKeyShowsOnlyItsEnd() {
        #expect(APIKeyMask.mask("AIzaSyD0123456789abcdefghijklmnopq") == "••••••••nopq")
    }
}

@Suite struct APIKeysStoreTests {
    func temp() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keys-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    @Test func savingKeepsTheFilePrivateAndBacksUpTheOldCopy() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        try store.save(file, over: stamp)
        #expect(try String(contentsOf: url, encoding: .utf8) == "export A=\"2\"\n")
        let mode = try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
        let backup = dir.appending(path: "backups/api_keys.bak")
        #expect(try String(contentsOf: backup, encoding: .utf8) == "export A=\"1\"\n")
        #expect(try FileManager.default.attributesOfItem(atPath: backup.path)[.posixPermissions] as? Int == 0o600)
    }

    @Test func anExistingBackupFolderIsMadePrivate() throws {
        let dir = try temp()
        let backups = dir.appending(path: "backups")
        try FileManager.default.createDirectory(at: backups, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o755])
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: backups)
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        try store.save(file, over: stamp)
        #expect(try FileManager.default.attributesOfItem(atPath: backups.path)[.posixPermissions] as? Int == 0o700)
    }

    @Test func aFileChangedSinceLoadingIsNotOverwritten() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        try "export A=\"edited in vim\"\n".write(to: url, atomically: true, encoding: .utf8)
        try file.set(name: "A", value: "2", provider: "P")
        #expect(throws: APIKeysError.changedOnDisk) { try store.save(file, over: stamp) }
        #expect(try String(contentsOf: url, encoding: .utf8) == "export A=\"edited in vim\"\n")
    }

    @Test func aSymlinkedFileIsWrittenThroughNotReplaced() throws {
        let dir = try temp()
        let real = dir.appending(path: "dotfiles/api_keys")
        try FileManager.default.createDirectory(at: real.deletingLastPathComponent(), withIntermediateDirectories: true)
        try "export A=\"1\"\n".write(to: real, atomically: true, encoding: .utf8)
        let link = dir.appending(path: ".api_keys")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: real)
        let store = APIKeysStore(url: link, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        try store.save(file, over: stamp)
        #expect(try FileManager.default.destinationOfSymbolicLink(atPath: link.path) == real.path)
        #expect(try String(contentsOf: real, encoding: .utf8) == "export A=\"2\"\n")
    }

    @Test func aFileThatIsNotUTF8IsNotRewritten() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try Data([0x23, 0x20, 0xff, 0xfe, 0x0a]).write(to: url)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        #expect(throws: APIKeysError.unreadable("the file isn't valid UTF-8, so Quiver won't rewrite it")) { try store.load() }
    }

    @Test func anEditDuringTheSaveIsNotOverwritten() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        try "export A=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        var store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        // An editor saves after the first check, while Quiver prepares its files.
        store.beforeReplace = { try? "export A=\"vim\"\n".write(to: url, atomically: true, encoding: .utf8) }
        var (file, stamp) = try store.load()
        try file.set(name: "A", value: "2", provider: "P")
        #expect(throws: APIKeysError.changedOnDisk) { try store.save(file, over: stamp) }
        #expect(try String(contentsOf: url, encoding: .utf8) == "export A=\"vim\"\n")
        let leftovers = try FileManager.default.contentsOfDirectory(atPath: dir.path).filter { $0.contains("quiver-") }
        #expect(leftovers.isEmpty)
    }

    @Test func aMissingFileLoadsEmptyAndIsCreatedPrivate() throws {
        let dir = try temp()
        let url = dir.appending(path: ".api_keys")
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        var (file, stamp) = try store.load()
        #expect(file.entries.isEmpty)
        try file.set(name: "A", value: "1", provider: "P")
        try store.save(file, over: stamp)
        #expect(try FileManager.default.attributesOfItem(atPath: url.path)[.posixPermissions] as? Int == 0o600)
    }
}

@Suite struct ShellEnvironmentTests {
    @Test func envOutputBecomesADictionary() {
        let env = LoginShell.parse(envOutput: "PATH=/usr/bin\nA=1=2\nEMPTY=\n")
        #expect(env == ["PATH": "/usr/bin", "A": "1=2", "EMPTY": ""])
    }
}

/// A shell that answers only when released.
actor GatedShell: ShellEnvironmentReading {
    private var gate: CheckedContinuation<Void, Never>?
    private(set) var waiting = false
    func freshEnvironment() async -> [String: String]? {
        waiting = true
        await withCheckedContinuation { gate = $0 }
        return ["OPENAI_ORG": "org-test"]
    }
    func release() { gate?.resume(); gate = nil }
}

/// A shell that reports a fixed environment.
struct FakeShell: ShellEnvironmentReading {
    var environment: [String: String]?
    func freshEnvironment() async -> [String: String]? { environment }
}

@MainActor
@Suite struct APIKeysModelTests {
    func model(text: String, shell: [String: String]? = [:]) throws -> (APIKeysModel, URL) {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keysmodel-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try text.write(to: url, atomically: true, encoding: .utf8)
        let store = APIKeysStore(url: url, backupDirectory: dir.appending(path: "backups"))
        return (APIKeysModel(store: store, shell: FakeShell(environment: shell)), url)
    }

    @Test func rowsCarryTheirShellStatus() async throws {
        let (model, _) = try model(text: sampleKeysFile, shell: [
            "ANTHROPIC_API_KEY": "sk-ant-test-0000000000000000abcd",
            "OPENAI_API_KEY": "something else",
        ])
        await model.load()
        #expect(model.rows.map(\.status) == [.loaded, .overridden, .notLoaded])
        #expect(model.providers == ["ANTHROPIC", "OPENAI"])
        #expect(model.problems.count == 1)
    }

    @Test func rowsShowBeforeTheShellAnswers() async throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "keysgate-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appending(path: ".api_keys")
        try sampleKeysFile.write(to: url, atomically: true, encoding: .utf8)
        let shell = GatedShell()
        let model = APIKeysModel(store: APIKeysStore(url: url, backupDirectory: dir), shell: shell)
        let load = Task { await model.load() }
        while !(await shell.waiting) { await Task.yield() }
        #expect(model.rows.count == 3)
        #expect(model.rows.allSatisfy { $0.status == .checking })
        await shell.release()
        await load.value
        #expect(model.rows.map(\.status) == [.notLoaded, .notLoaded, .loaded])
    }

    @Test func anUnreadableShellLeavesTheStatusUnknown() async throws {
        let (model, _) = try model(text: sampleKeysFile, shell: nil)
        await model.load()
        #expect(model.rows.allSatisfy { $0.status == .unknown })
    }

    @Test func searchMatchesProviderAndName() async throws {
        let (model, _) = try model(text: sampleKeysFile)
        await model.load()
        model.search = "org"
        #expect(model.visible.map(\.name) == ["OPENAI_ORG"])
        model.search = "anthropic"
        #expect(model.visible.map(\.name) == ["ANTHROPIC_API_KEY"])
    }

    @Test func savingWritesTheFileAndReloads() async throws {
        let (model, url) = try model(text: sampleKeysFile)
        await model.load()
        try await model.set(name: "MISTRAL_API_KEY", value: "m-1", provider: "MISTRAL")
        #expect(try String(contentsOf: url, encoding: .utf8).contains("export MISTRAL_API_KEY=\"m-1\""))
        #expect(model.rows.map(\.name).contains("MISTRAL_API_KEY"))
        try await model.remove(name: "MISTRAL_API_KEY")
        #expect(!model.rows.map(\.name).contains("MISTRAL_API_KEY"))
    }

    @Test func anEditOverAnOutsideChangeIsRefused() async throws {
        let (model, url) = try model(text: sampleKeysFile)
        await model.load()
        try "export X=\"1\"\n".write(to: url, atomically: true, encoding: .utf8)
        await #expect(throws: APIKeysError.changedOnDisk) {
            try await model.set(name: "OPENAI_ORG", value: "o", provider: "OPENAI")
        }
    }
}
