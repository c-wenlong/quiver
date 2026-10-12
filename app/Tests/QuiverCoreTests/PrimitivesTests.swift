import Foundation
import Testing
@testable import QuiverCore

/// A home laid out like the real one: `~/.quiver/plugins` for the hub.
final class PrimitiveHome {
    let home: URL

    init() throws {
        home = FileManager.default.temporaryDirectory
            .appending(path: "quiver-prim-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        try FileManager.default.createDirectory(
            at: home.appending(path: ".quiver/plugins"), withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: home) }

    @discardableResult
    func dir(_ path: String) throws -> URL {
        let url = home.appending(path: path)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    @discardableResult
    func write(_ path: String, _ text: String) throws -> URL {
        let file = home.appending(path: path)
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try Data(text.utf8).write(to: file)
        return file
    }
}

@Suite struct OwnershipTests {
    @Test func aDirectorySourceUnderQuiverPluginsIsEditable() throws {
        let t = try PrimitiveHome()
        let market = try t.dir(".quiver/plugins/development")
        let m = PluginMarketplace(name: "development", hub: true, path: market.path,
                                  harnesses: ["claude": .init(sourceType: "directory", location: market.path)],
                                  plugins: [])
        #expect(PrimitiveOwnership.marketplaceDirectory(m, home: t.home)?.path
            == FolderOwners.realPath(market.path))
        #expect(PrimitiveOwnership.isEditable(m, home: t.home))
    }

    @Test func aGitHubSourceIsNotEditable() throws {
        let t = try PrimitiveHome()
        let m = PluginMarketplace(name: "claude-plugins-official",
                                  path: t.home.appending(path: ".claude/plugins/marketplaces/official").path,
                                  harnesses: ["claude": .init(sourceType: "github",
                                                              location: "owner/repo")], plugins: [])
        #expect(!PrimitiveOwnership.isEditable(m, home: t.home))
    }

    @Test func aPluginInsideAnEditableMarketplaceIsEditable() throws {
        let t = try PrimitiveHome()
        try t.dir(".quiver/plugins/development/eng")
        let m = PluginMarketplace(name: "development", hub: true,
                                  path: t.home.appending(path: ".quiver/plugins/development").path,
                                  harnesses: [:], plugins: [])
        let plugin = CatalogPlugin(name: "eng", marketplace: "development",
                                   sourcePath:
                                    t.home.appending(path: ".quiver/plugins/development/eng").path,
                                   harnesses: [:])
        #expect(PrimitiveOwnership.pluginDirectory(plugin, in: m, home: t.home) != nil)
    }

    @Test func aPluginWithoutALocalFolderIsNotEditable() throws {
        let t = try PrimitiveHome()
        try t.dir(".quiver/plugins/development")
        let m = PluginMarketplace(name: "development", hub: true,
                                  path: t.home.appending(path: ".quiver/plugins/development").path,
                                  harnesses: [:], plugins: [])
        let remote = CatalogPlugin(name: "p", marketplace: "development",
                                   sourcePath: nil, harnesses: [:])
        #expect(PrimitiveOwnership.pluginDirectory(remote, in: m, home: t.home) == nil)
        let outside = CatalogPlugin(name: "o", marketplace: "development",
                                    sourcePath: "/opt/elsewhere", harnesses: [:])
        #expect(PrimitiveOwnership.pluginDirectory(outside, in: m, home: t.home) == nil)
    }

    @Test func aSkillUnderQuiverIsEditableASymlinkOutsideIsNot() throws {
        let t = try PrimitiveHome()
        try t.dir(".quiver/skills/mine")
        try t.dir("elsewhere")
        try FileManager.default.createSymbolicLink(
            atPath: t.home.appending(path: ".quiver/skills/linked").path,
            withDestinationPath: t.home.appending(path: "elsewhere").path)
        #expect(PrimitiveOwnership.isEditable(skillPath: t.home.appending(path: ".quiver/skills/mine").path,
                                            home: t.home))
        #expect(!PrimitiveOwnership.isEditable(skillPath: t.home.appending(path: ".quiver/skills/linked").path,
                                             home: t.home))
        #expect(!PrimitiveOwnership.isEditable(skillPath: t.home.appending(path: ".quiver/cache/x").path,
                                             home: t.home))
        #expect(!PrimitiveOwnership.isEditable(skillPath: t.home.appending(path: ".claude/skills/theirs").path,
                                             home: t.home))
    }
}

@Suite struct DisplayNamesTests {
    @Test func roundTripAndClear() throws {
        let t = try PrimitiveHome()
        let file = PluginDisplayNames.file(home: t.home)
        var names = PluginDisplayNames()
        names.set("My Marketplace", forMarketplace: "development")
        names.set("Engineering", forPlugin: "eng@development")
        try names.save(to: file)
        let loaded = PluginDisplayNames.load(from: file)
        #expect(loaded == names)
        #expect(loaded.name(forMarketplace: "development") == "My Marketplace")
        #expect(loaded.name(forPlugin: "eng@development") == "Engineering")
        var cleared = loaded
        cleared.set("", forPlugin: "eng@development")
        #expect(cleared.name(forPlugin: "eng@development") == nil)
        try cleared.save(to: file)
        #expect(PluginDisplayNames.load(from: file).plugins.isEmpty)
    }

    @Test func missingFileLoadsEmpty() throws {
        let t = try PrimitiveHome()
        #expect(PluginDisplayNames.load(from: PluginDisplayNames.file(home: t.home)) == PluginDisplayNames())
    }
}

@Suite struct JSONTextEditTests {
    @Test func replacesAValueKeepingOrderAndUnknownKeys() throws {
        let text = """
        {
          "name": "eng",
          "weird": {"kept": true},
          "version": "1.0.0"
        }
        """
        let edited = try JSONTextEdit.set("2.0.0", at: ["version"], in: text)
        #expect(edited.contains("\"name\": \"eng\""))
        #expect(edited.contains("\"weird\": {\"kept\": true}"))
        #expect(edited.contains("\"version\": \"2.0.0\""))
        // Unknown key still sits between name and version.
        let order = edited.range(of: "\"weird\"")!.lowerBound
        #expect(edited.range(of: "\"version\"")!.lowerBound > order)
    }

    @Test func insertsANewKeyAndCreatesParents() throws {
        let text = "{\n  \"name\": \"eng\"\n}\n"
        let edited = try JSONTextEdit.set("Shown", at: ["interface", "displayName"], in: text)
        #expect(edited.contains("\"name\": \"eng\""))
        #expect(edited.contains("\"interface\": {\"displayName\": \"Shown\"}"))
        let object = try JSONSerialization.jsonObject(with: Data(edited.utf8)) as? [String: Any]
        #expect((object?["interface"] as? [String: Any])?["displayName"] as? String == "Shown")
    }

    @Test func insertsIntoAnExistingObject() throws {
        let text = "{\n  \"author\": {\n    \"name\": \"kai\"\n  }\n}\n"
        let edited = try JSONTextEdit.set("k@x.dev", at: ["author", "email"], in: text)
        let object = try JSONSerialization.jsonObject(with: Data(edited.utf8)) as? [String: Any]
        let author = object?["author"] as? [String: Any]
        #expect(author?["name"] as? String == "kai")
        #expect(author?["email"] as? String == "k@x.dev")
    }
}

@Suite struct ManifestFieldsTests {
    @Test func applyPreservesUnknownKeysAndOrder() throws {
        let text = """
        {
          "name": "eng",
          "extra": [1, 2],
          "author": "old",
          "version": "1.0.0"
        }
        """
        var fields = PluginManifestFields(parsing: text)
        fields.version = "2.0.0"
        fields.authorName = "Kai"
        fields.authorEmail = "k@x.dev"
        let edited = try fields.apply(to: text)
        let parsed = PluginManifestFields(parsing: edited)
        #expect(parsed.version == "2.0.0")
        #expect(parsed.authorName == "Kai")
        #expect(parsed.authorEmail == "k@x.dev")
        // `extra` untouched and still ahead of `author` in the file.
        #expect(edited.contains("\"extra\": [1, 2]"))
        #expect(edited.range(of: "\"extra\"")!.lowerBound < edited.range(of: "\"author\"")!.lowerBound)
    }

    @Test func versionAndURLValidation() {
        #expect(PluginManifestFields.isVersion("1.2.3"))
        #expect(PluginManifestFields.isVersion("1.2.3-beta.1"))
        #expect(!PluginManifestFields.isVersion("v2"))
        #expect(PluginManifestFields.isURL("https://example.com/x"))
        #expect(!PluginManifestFields.isURL("not a url"))
        var fields = PluginManifestFields()
        fields.version = "abc"
        fields.homepage = "nope"
        #expect(fields.problems.count == 2)
    }

    @Test func codexDisplayNameKeepsOtherKeys() throws {
        let text = "{\n  \"name\": \"eng\",\n  \"interface\": {\n    \"category\": \"dev\"\n  }\n}\n"
        let edited = try PluginManifestFields.setCodexDisplayName("Engineering", in: text)
        let object = try JSONSerialization.jsonObject(with: Data(edited.utf8)) as? [String: Any]
        let interface = object?["interface"] as? [String: Any]
        #expect(interface?["displayName"] as? String == "Engineering")
        #expect(interface?["category"] as? String == "dev")
    }
}

@Suite struct FrontmatterTests {
    let skill = """
    ---
    name: tdd
    description: Red, green, refactor.
    license: MIT
    ---

    # Body

    Keep me.
    """

    @Test func editsAFieldKeepingBodyAndUnknownKeys() {
        let edited = MarkdownFrontmatter.update(skill, set: [(key: "description", value: "New words.")])
        #expect(edited.contains("description: New words."))
        #expect(edited.contains("name: tdd"))
        #expect(edited.contains("license: MIT"))
        #expect(edited.contains("# Body\n\nKeep me."))
    }

    @Test func addsAMissingKeyBeforeTheClose() {
        let edited = MarkdownFrontmatter.update(skill, set: [(key: "tags", value: "testing")])
        #expect(edited.contains("tags: testing\n---"))
    }

    @Test func aComplexFieldIsLeftAlone() {
        let text = "---\nname: x\nrefs:\n  - a\n  - b\n---\nbody\n"
        let front = MarkdownFrontmatter(text: text)
        #expect(front?.fields.map(\.key) == ["name"])
        #expect(front?.other == ["refs:", "  - a", "  - b"])
        // A complex field is not rewritten and never duplicated.
        let edited = MarkdownFrontmatter.update(text, set: [(key: "refs", value: "oops")])
        #expect(edited == text)
        #expect(edited.contains("refs:\n  - a\n  - b"))
    }

    @Test func createsFrontmatterWhenMissing() {
        let edited = MarkdownFrontmatter.update("# Hi\n", set: [(key: "name", value: "s")])
        #expect(edited.hasPrefix("---\nname: s\n---\n\n# Hi"))
    }
}

@Suite struct PrimitivePartsTests {
    @Test func nameValidation() {
        #expect(PrimitiveParts.isValidName("tdd"))
        #expect(PrimitiveParts.isValidName("code-review-2"))
        #expect(!PrimitiveParts.isValidName("Code Review"))
        #expect(!PrimitiveParts.isValidName("-lead"))
        #expect(!PrimitiveParts.isValidName("trail-"))
        #expect(!PrimitiveParts.isValidName("has space"))
    }

    @Test func createsASkillWithFrontmatter() throws {
        let t = try PrimitiveHome()
        let root = try t.dir("plugin")
        let file = try PrimitiveParts.create(.skill, named: "tdd", in: root)
        #expect(file.lastPathComponent == "SKILL.md")
        let text = try String(contentsOf: file, encoding: .utf8)
        #expect(text.hasPrefix("---\nname: tdd\ndescription:"))
        #expect(throws: Error.self) { try PrimitiveParts.create(.skill, named: "tdd", in: root) }
    }

    @Test func createsCommandAndAgentFiles() throws {
        let t = try PrimitiveHome()
        let root = try t.dir("plugin")
        #expect(try PrimitiveParts.create(.command, named: "go", in: root)
            .path.hasSuffix("commands/go.md"))
        let agent = try PrimitiveParts.create(.agent, named: "review", in: root)
        #expect(agent.path.hasSuffix("agents/review.md"))
        #expect(try String(contentsOf: agent, encoding: .utf8).contains("name: review"))
    }

    @Test func deleteGoesThroughTheTrashFunction() throws {
        let t = try PrimitiveHome()
        let file = try t.write("plugin/commands/x.md", "x")
        var trashed: [String] = []
        try PrimitiveParts.trash(file) { trashed.append($0.path) }
        #expect(trashed == [file.path])
        #expect(FileManager.default.fileExists(atPath: file.path))
    }
}

@Suite struct PrimitiveTreeTests {
    @Test func groupsHidesAndCounts() throws {
        let t = try PrimitiveHome()
        let root = try t.dir("plugin")
        try t.write("plugin/skills/tdd/SKILL.md", "---\nname: tdd\n---\n")
        try t.write("plugin/skills/review/SKILL.md", "---\nname: review\n---\n")
        try t.write("plugin/commands/ship.md", "# Ship\n")
        try t.write("plugin/.claude-plugin/plugin.json", "{}")
        try t.write("plugin/.git/config", "x")
        try t.write("plugin/notes.md", "n")
        try t.write("plugin/.hidden", "x")
        let tree = PrimitiveTree(root: root)
        let kinds = tree.sections.map(\.kind)
        #expect(kinds == [.skills, .commands, .other])
        let skills = tree.sections.first { $0.kind == .skills }
        #expect(skills?.entries.map(\.name) == ["review", "tdd"])
        #expect(skills?.entries.first?.isFolder == true)
        #expect(skills?.entries.first?.previewPath.hasSuffix("SKILL.md") == true)
        let others = tree.sections.first { $0.kind == .other }?.entries.map(\.name)
        #expect(others == ["notes.md"])
        #expect(tree.sections.flatMap(\.entries).allSatisfy { !$0.name.hasPrefix(".") })
    }

    @Test func binaryAndLargeFilesAreNotText() throws {
        let t = try PrimitiveHome()
        let png = try t.write("bin/img.png", "not a png")
        try Data([0x89, 0x50, 0x00, 0x01]).write(to: png)
        #expect(!PrimitiveTree.isTextFile(png))
        let big = t.home.appending(path: "bin/big.txt")
        try Data(count: 1_100_000).write(to: big)
        #expect(!PrimitiveTree.isTextFile(big))
        let md = try t.write("bin/readme.md", "# ok\n")
        #expect(PrimitiveTree.isTextFile(md))
    }
}
