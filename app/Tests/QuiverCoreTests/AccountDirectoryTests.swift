import Foundation
import Testing
@testable import QuiverCore

@Suite struct AccountDirectoryTests {
    let home = URL(fileURLWithPath: "/Users/k")

    func directory(_ profiles: [Profile]) -> AccountDirectory {
        let store = ProfileStore(root: home.appending(path: ".quiver/accounts"), userHome: home)
        return AccountDirectory(profiles: profiles, paths: store.paths(for:), home: home)
    }

    var profiles: [Profile] {
        [
            Profile(id: "claude-default", vendor: .claude, name: "Default", isDefault: true, color: "#cf6848"),
            Profile(id: "claude-work", vendor: .claude, name: "Work", isDefault: false, color: "#3b7ff0"),
            Profile(id: "codex-default", vendor: .codex, name: "Default", isDefault: true, color: "#139f8b"),
            Profile(id: "codex-side", vendor: .codex, name: "Side", isDefault: false, color: "#c4860f"),
        ]
    }

    @Test func rootsCoverTheStandardHomesAndEveryExtraProfile() {
        let roots = directory(profiles).roots
        #expect(roots.claudeHomes.map(\.path) == ["/Users/k/.claude", "/Users/k/.quiver/accounts/claude/claude-work/home"])
        #expect(roots.codexHomes.map(\.path) == ["/Users/k/.codex", "/Users/k/.quiver/accounts/codex/codex-side/home"])
        #expect(roots.openCodeDatabases.count == 1)
        #expect(roots.devinDatabases.count == 1)
    }

    @Test func homesAreNamedAfterTheirProfile() {
        let names = directory(profiles)
        #expect(names.label(for: "/Users/k/.claude") == "Claude: Default")
        #expect(names.label(for: "/Users/k/.quiver/accounts/codex/codex-side/home") == "Codex: Side")
    }

    @Test func homesWithoutAProfileShowAShortPath() {
        let names = directory([])
        #expect(names.label(for: "/Users/k/.claude") == "~/.claude")
        #expect(names.label(for: "/Users/k/.local/share/opencode") == "OpenCode")
        #expect(names.label(for: "/Users/k/.local/share/devin/cli") == "Devin")
        #expect(names.label(for: "/opt/elsewhere") == "/opt/elsewhere")
    }

    @Test func theDirectoryRebuildsOnlyWhenProfilesChange() throws {
        let root = FileManager.default.temporaryDirectory
            .appending(path: "dir-cache-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let file = root.appending(path: "profiles.json")
        try "[]".write(to: file, atomically: true, encoding: .utf8)
        let store = ProfileStore(root: root, userHome: home)
        final class Builds: @unchecked Sendable { var value = 0 }
        let builds = Builds()
        let cache = CachedAccountDirectory(file: file) {
            builds.value += 1
            return AccountDirectory(profiles: [], paths: store.paths(for:), home: self.home)
        }
        // Same mtime answers from the cache: one build for any reads.
        _ = cache.current
        _ = cache.current
        #expect(builds.value == 1)
        // A changed stamp rebuilds once, then settles again.
        try FileManager.default.setAttributes(
            [.modificationDate: Date(timeIntervalSince1970: 2_000_000_000)],
            ofItemAtPath: file.path)
        _ = cache.current
        #expect(builds.value == 2)
        _ = cache.current
        #expect(builds.value == 2)
    }
}

@Suite struct UsageFormatTests {
    @Test func tokensAreCompact() {
        #expect(UsageFormat.tokens(0) == "0")
        #expect(UsageFormat.tokens(950) == "950")
        #expect(UsageFormat.tokens(12_340) == "12.3K")
        #expect(UsageFormat.tokens(4_200_000) == "4.2M")
        #expect(UsageFormat.tokens(11_600_000_000) == "11.6B")
        #expect(UsageFormat.tokens(1_000_000) == "1M")
    }

    @Test func dollarsKeepCentsOnlyWhenSmall() {
        #expect(UsageFormat.dollars(0) == "$0")
        #expect(UsageFormat.dollars(0.004) == "<$0.01")
        #expect(UsageFormat.dollars(3.456) == "$3.46")
        #expect(UsageFormat.dollars(4687.2) == "$4,687")
    }

    @Test func harnessesHaveReadableNames() {
        #expect(UsageFormat.harness("claude") == "Claude Code")
        #expect(UsageFormat.harness("codex") == "Codex")
        #expect(UsageFormat.harness("opencode") == "OpenCode")
        #expect(UsageFormat.harness("devin") == "Devin")
        #expect(UsageFormat.harness("gemini") == "Gemini")
    }
}
