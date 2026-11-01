import Foundation
import Testing
@testable import SWECore

/// Edge branches in the pure models: title tables, decoders, parsers and
/// text editors that sit below the page models.

@Suite("Skill root states")
struct SkillRootEdgeTests {
    @Test func explanationPerState() {
        func root(_ state: String, detail: String = "", archived: Bool = false) -> SkillRoot {
            SkillRoot(name: "x", path: "/r", state: state, detail: detail, archived: archived)
        }
        #expect(root("linked").explanation.contains("shared skills"))
        #expect(root("linked", archived: true).explanation.contains("Archived"))
        #expect(root("create").explanation.contains("Linking creates"))
        #expect(root("create", archived: true).explanation.contains("Archived"))
        #expect(root("relink", detail: "points at ~/other").explanation.contains("~/other"))
        #expect(root("relink").explanation == "Points somewhere else.")
        #expect(root("absorb", detail: "empty").explanation.contains("empty"))
        #expect(root("absorb", detail: "2 skills").explanation.contains("backs it up"))
        #expect(root("keep", detail: "3 skills").explanation.contains("nowhere else"))
        #expect(root("ignored").explanation == "Ignored by swe init.")
        #expect(root("ignored", detail: "a reason").explanation.contains("a reason"))
        #expect(root("skipped").explanation.contains("isn't installed"))
        #expect(root("weird").explanation == "weird")
        #expect(root("weird", detail: "why").explanation == "weird: why")
    }

    @Test func linkNeedsForceOnlyForNonemptyAbsorb() {
        #expect(SkillRoot(name: "x", path: "/r", state: "absorb", detail: "2 skills").linkNeedsForce)
        #expect(!SkillRoot(name: "x", path: "/r", state: "absorb", detail: "empty").linkNeedsForce)
        #expect(!SkillRoot(name: "x", path: "/r", state: "linked").linkNeedsForce)
    }

    @Test func sourceTitles() {
        #expect(Set(Skill.Source.allCases.map(\.title)).count == Skill.Source.allCases.count)
        #expect(Skill.Source.builtin.title == "Built in")
        #expect(Skill.Source.catalog.title == "Catalog")
    }
}

@Suite("Primitive tree")
struct PrimitiveTreeEdgeTests {
    @Test func kindTitles() {
        #expect(PrimitiveTree.Kind.allCases.map(\.title) == [
            "Skills", "Commands", "Agents", "Hooks", "Other files",
        ])
    }

    @Test func sectionsGroupKnownFolders() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let fm = FileManager.default
        let skills = root.appending(path: "skills/tdd")
        try fm.createDirectory(at: skills, withIntermediateDirectories: true)
        try "skill".write(to: skills.appending(path: "SKILL.md"), atomically: true, encoding: .utf8)
        try "x".write(to: skills.appending(path: "ref.md"), atomically: true, encoding: .utf8)
        try fm.createDirectory(at: root.appending(path: "commands"), withIntermediateDirectories: true)
        try "c".write(to: root.appending(path: "commands/run.md"), atomically: true, encoding: .utf8)
        try "o".write(to: root.appending(path: "loose.md"), atomically: true, encoding: .utf8)
        try ".hidden".write(to: root.appending(path: "commands/.hidden"), atomically: true, encoding: .utf8)

        let tree = PrimitiveTree(root: root)
        let byKind = Dictionary(tree.sections.map { ($0.kind, $0.entries) }, uniquingKeysWith: { a, _ in a })
        let skillEntry = try #require(byKind[.skills]?.first)
        #expect(skillEntry.isFolder)
        #expect(skillEntry.previewPath.hasSuffix("SKILL.md"))
        #expect(skillEntry.children.map(\.name).contains("ref.md"))
        #expect(byKind[.commands]?.map(\.name) == ["run.md"])
        #expect(byKind[.other]?.map(\.name) == ["loose.md"])
    }

    @Test func nestedFolderInsideCommandsListsInnerFiles() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let nested = root.appending(path: "commands/group")
        try FileManager.default.createDirectory(at: nested, withIntermediateDirectories: true)
        try "i".write(to: nested.appending(path: "inner.md"), atomically: true, encoding: .utf8)
        let tree = PrimitiveTree(root: root)
        let names = tree.sections.first(where: { $0.kind == .commands })?.entries.map(\.name) ?? []
        #expect(names == ["group/inner.md"])
    }

    @Test func isTextFile() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let dir = temp.url
        let text = dir.appending(path: "a.md")
        try "hello".write(to: text, atomically: true, encoding: .utf8)
        let binary = dir.appending(path: "b.bin")
        try Data([0, 1, 2]).write(to: binary)
        let missing = dir.appending(path: "nope.txt")
        #expect(PrimitiveTree.isTextFile(text))
        #expect(!PrimitiveTree.isTextFile(binary))
        #expect(!PrimitiveTree.isTextFile(missing))
    }
}

@Suite("Plugin models")
struct PluginEdgeTests {
    @Test func statusTitlesAndMapping() {
        #expect(PluginHarnessState.Status.enabled.title == "Enabled")
        #expect(PluginHarnessState.Status.absent.title == "Not listed")
        #expect(PluginHarnessState(available: true, installed: true, enabled: false).status == .disabled)
        #expect(PluginHarnessState(available: true, installed: true).status == .installed)
        #expect(PluginHarnessState(available: true, installed: false).status == .available)
        #expect(PluginHarnessState(available: false, installed: false).status == .absent)
        #expect(PluginHarnessState(available: false, installed: nil, path: "/cached").status == .cached)
    }

    @Test func catalogEntryDerivesRefAndToleratesBadTypes() throws {
        let json = """
        {"name": "demo", "marketplace": "mp", "displayName": 42, "tags": "oops"}
        """
        let entry = try JSONDecoder().decode(CatalogPlugin.self, from: Data(json.utf8))
        #expect(entry.ref == "demo@mp")
        #expect(entry.displayName == nil)
    }

    @Test func driftSeverityOrdersWorstFirst() {
        #expect(PluginDriftState.Kind.unregistered.severity < PluginDriftState.Kind.installed.severity)
        #expect(PluginDriftState.Kind.disabled.severity < PluginDriftState.Kind.unknown.severity)
    }
}

@Suite("JSON text edit")
struct JSONTextEditEdgeTests {
    @Test func setReplacesScalarInPlace() throws {
        let text = "{\n  \"a\": 1,\n  \"b\": \"x\"\n}\n"
        let out = try JSONTextEdit.set(2, at: ["a"], in: text)
        #expect(out.contains("\"a\": 2"))
        #expect(out.contains("\"b\": \"x\""))
    }

    @Test func setInsertsMissingKey() throws {
        let text = "{\"a\": 1}"
        let out = try JSONTextEdit.set("v", at: ["new"], in: text)
        #expect(out.contains("\"new\""))
    }

    @Test func setRejectsNonObject() {
        #expect(throws: JSONTextEdit.Failure.self) {
            _ = try JSONTextEdit.set(1, at: ["a"], in: "[1,2]")
        }
    }

    @Test func setRejectsUnrenderableValue() {
        #expect(throws: JSONTextEdit.Failure.self) {
            _ = try JSONTextEdit.set(Date(), at: ["a"], in: "{}")
        }
    }

    @Test func setBoolRendersBare() throws {
        let out = try JSONTextEdit.set(true, at: ["flag"], in: "{}")
        #expect(out.contains("true"))
    }
}

@Suite("Overview periods")
struct OverviewPeriodEdgeTests {
    @Test func titlesAndRanges() {
        #expect(OverviewPeriod.today.title == "Today")
        #expect(OverviewPeriod.allTime.title == "All time")
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(secondsFromGMT: 0)!
        let now = Date(timeIntervalSince1970: 1_800_000_000)
        for period in OverviewPeriod.allCases {
            let range = period.range(now: now, calendar: cal)
            #expect(range.end >= range.start)
        }
        let today = OverviewPeriod.today.range(now: now, calendar: cal)
        #expect(cal.isDate(today.start, inSameDayAs: now))
    }
}

@Suite("Sessions window")
struct SessionWindowTests {
    @Test func titles() {
        #expect(SessionsModel.Window.week.title == "7 days")
        #expect(SessionsModel.Window.all.title == "All time")
        #expect(Set(SessionsModel.Window.allCases.map(\.title)).count == 6)
    }
}

@Suite("MCP helpers")
struct McpModelEdgeTests {
    @Test func filterTitles() {
        #expect(McpFilter.all.title == "All")
        #expect(McpFilter.drift.title == "Out of sync")
        #expect(McpFilter.conflicts.title == "Conflicts")
    }

    @Test func gitStateRejectsNonRepoOutput() {
        let state = GitState.parse(porcelain: "fatal: not a git repository\n")
        #expect(!state.isRepo)
        let clean = GitState.parse(porcelain: "## main\n")
        #expect(clean.isRepo)
    }

    @Test func codeEditorInstalledHonoursProbe() {
        let all = CodeEditors.installed { _ in true }
        let none = CodeEditors.installed { _ in false }
        #expect(none.isEmpty)
        #expect(!all.isEmpty)
    }
}

@Suite("API key errors")
struct APIKeyErrorTests {
    @Test func descriptionsNameTheProblem() {
        #expect(APIKeysError.invalidName("2bad").errorDescription?.contains("2bad") == true)
        #expect(APIKeysError.duplicate("KEY").errorDescription?.contains("KEY") == true)
        #expect(APIKeysError.notFound("GONE").errorDescription?.contains("GONE") == true)
        #expect(APIKeysError.nameTaken("K").errorDescription?.contains("K") == true)
        #expect(APIKeysError.providerTaken("P").errorDescription?.contains("P") == true)
        #expect(APIKeysError.multilineValue.errorDescription != nil)
        #expect(APIKeysError.changedOnDisk.errorDescription != nil)
        #expect(APIKeysError.unreadable("io").errorDescription?.contains("io") == true)
        #expect(APIKeysError.unwritable("io").errorDescription?.contains("io") == true)
        #expect(APIKeysError.invalidProvider.errorDescription != nil)
    }
}

@Suite("Manifest fields")
struct ManifestFieldTests {
    @Test func isURL() {
        #expect(PluginManifestFields.isURL("https://example.com/x"))
        #expect(PluginManifestFields.isVersion("1.2.3-beta.1"))
        #expect(!PluginManifestFields.isURL("not a url"))
        #expect(!PluginManifestFields.isURL(""))
        #expect(!PluginManifestFields.isVersion("1.2"))
    }

    @Test func applyWritesManifestFields() throws {
        var fields = PluginManifestFields()
        fields.description = "A thing"
        fields.version = "1.2.3"
        let out = try fields.apply(to: "{\"name\":\"x\"}\n")
        #expect(out.contains("A thing"))
        #expect(out.contains("1.2.3"))
        #expect(out.contains("name"))
        // Markdown files without frontmatter are untouched by range-finding.
        #expect(MarkdownFrontmatter(text: "no frontmatter") == nil)
    }
}

@Suite("Codex parser")
struct CodexParserEdgeTests {
    @Test func normalizedTierCases() {
        #expect(CodexFileParser.normalizedTier("Priority") == "priority")
        #expect(CodexFileParser.normalizedTier("flex") == "flex")
        #expect(CodexFileParser.normalizedTier("Unknown-Tier") == "unknown-tier")
    }
}
