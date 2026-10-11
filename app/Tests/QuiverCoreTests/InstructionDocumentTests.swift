import Foundation
import Testing
@testable import QuiverCore

/// A fresh folder per test, removed afterwards.
final class TempFolder {
    let url: URL

    init() throws {
        url = FileManager.default.temporaryDirectory
            .appending(path: "quiver-tests-\(UUID().uuidString)")
            .resolvingSymlinksInPath()
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: url) }

    func write(_ name: String, _ text: String) throws -> URL {
        let file = url.appending(path: name)
        try Data(text.utf8).write(to: file)
        return file
    }

    func read(_ name: String) throws -> String {
        try String(contentsOf: url.appending(path: name), encoding: .utf8)
    }

    func link(_ name: String, to destination: String) throws {
        try FileManager.default.createSymbolicLink(atPath: url.appending(path: name).path,
                                                   withDestinationPath: destination)
    }

    func isLink(_ name: String) -> Bool {
        (try? FileManager.default.destinationOfSymbolicLink(atPath: url.appending(path: name).path)) != nil
    }
}

@MainActor
@Suite struct InstructionDocumentTests {
    @Test func loadsAndSavesInPlace() throws {
        let folder = try TempFolder()
        let file = try folder.write("AGENTS.md", "# Rules\n")
        let doc = InstructionDocument(url: file)
        doc.load()
        #expect(doc.state == .ready)
        #expect(doc.text == "# Rules\n")
        #expect(!doc.isDirty)

        doc.text += "Be brief.\n"
        #expect(doc.isDirty)
        #expect(doc.save() == .saved)
        #expect(!doc.isDirty)
        #expect(try folder.read("AGENTS.md") == "# Rules\nBe brief.\n")
    }

    @Test func refusesToOverwriteAChangeMadeOnDiskSinceOpening() throws {
        let folder = try TempFolder()
        let file = try folder.write("AGENTS.md", "one\n")
        let doc = InstructionDocument(url: file)
        doc.load()
        doc.text = "mine\n"
        _ = try folder.write("AGENTS.md", "theirs\n")

        #expect(doc.save() == .changedOnDisk)
        #expect(doc.changedOnDisk)
        #expect(try folder.read("AGENTS.md") == "theirs\n")
        #expect(doc.text == "mine\n")

        #expect(doc.save(overwrite: true) == .saved)
        #expect(!doc.changedOnDisk)
        #expect(try folder.read("AGENTS.md") == "mine\n")
    }

    @Test func aFileCreatedElsewhereWhileNewIsAlsoAConflict() throws {
        let folder = try TempFolder()
        let doc = InstructionDocument(url: folder.url.appending(path: "AGENTS.md"))
        doc.load()
        #expect(doc.state == .ready)
        #expect(!doc.existsOnDisk)
        doc.text = "new\n"
        _ = try folder.write("AGENTS.md", "someone else's\n")
        #expect(doc.save() == .changedOnDisk)
    }

    @Test func savingANewFileCreatesIt() throws {
        let folder = try TempFolder()
        let doc = InstructionDocument(url: folder.url.appending(path: "CLAUDE.md"))
        doc.load()
        doc.text = "hello\n"
        #expect(doc.save() == .saved)
        #expect(doc.existsOnDisk)
        #expect(try folder.read("CLAUDE.md") == "hello\n")
    }

    @Test func checkDiskPicksUpOutsideEditsWhenThereIsNothingToLose() throws {
        let folder = try TempFolder()
        let file = try folder.write("AGENTS.md", "v1\n")
        let doc = InstructionDocument(url: file)
        doc.load()
        _ = try folder.write("AGENTS.md", "v2\n")
        doc.checkDisk()
        #expect(doc.text == "v2\n")
        #expect(!doc.changedOnDisk)
    }

    @Test func checkDiskFlagsOutsideEditsWhenThereAreUnsavedOnes() throws {
        let folder = try TempFolder()
        let file = try folder.write("AGENTS.md", "v1\n")
        let doc = InstructionDocument(url: file)
        doc.load()
        doc.text = "draft\n"
        _ = try folder.write("AGENTS.md", "v2\n")
        doc.checkDisk()
        #expect(doc.changedOnDisk)
        #expect(doc.text == "draft\n")
    }

    @Test func savingThroughASymlinkWritesTheTargetAndKeepsTheLink() throws {
        let folder = try TempFolder()
        _ = try folder.write("AGENTS.md", "shared\n")
        try folder.link("CLAUDE.md", to: "AGENTS.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "CLAUDE.md"))
        doc.load()
        #expect(doc.text == "shared\n")
        #expect(doc.linkTarget?.lastPathComponent == "AGENTS.md")

        doc.text = "edited\n"
        #expect(doc.save() == .saved)
        #expect(folder.isLink("CLAUDE.md"))
        #expect(try folder.read("AGENTS.md") == "edited\n")
    }

    @Test func aDanglingLinkIsWrittenAtItsTargetNotReplaced() throws {
        let folder = try TempFolder()
        try folder.link("CLAUDE.md", to: "AGENTS.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "CLAUDE.md"))
        doc.load()
        doc.text = "first\n"
        #expect(doc.save() == .saved)
        #expect(folder.isLink("CLAUDE.md"))
        #expect(try folder.read("AGENTS.md") == "first\n")
    }

    @Test func aCircularLinkIsNeverReplacedByASave() throws {
        let folder = try TempFolder()
        try folder.link("A.md", to: "B.md")
        try folder.link("B.md", to: "A.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "A.md"))
        doc.load()
        guard case .failed(let message) = doc.state else {
            Issue.record("expected failed, got \(doc.state)")
            return
        }
        #expect(message.contains("link"))
        doc.text = "x"
        guard case .failed = doc.save(overwrite: true) else { Issue.record("save went through"); return }
        #expect(folder.isLink("A.md") && folder.isLink("B.md"))
    }

    @Test func checkDiskRetriesAFileThatFailedOnceItIsRepaired() throws {
        let folder = try TempFolder()
        try folder.link("A.md", to: "B.md")
        try folder.link("B.md", to: "A.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "A.md"))
        doc.load()
        guard case .failed = doc.state else { Issue.record("expected failed"); return }
        try FileManager.default.removeItem(at: folder.url.appending(path: "B.md"))
        _ = try folder.write("B.md", "fixed\n")
        doc.checkDisk()
        #expect(doc.state == .ready)
        #expect(doc.text == "fixed\n")
    }

    @Test func revertDropsEdits() throws {
        let folder = try TempFolder()
        let doc = InstructionDocument(url: try folder.write("AGENTS.md", "keep\n"))
        doc.load()
        doc.text = "oops"
        doc.revert()
        #expect(doc.text == "keep\n")
        #expect(!doc.isDirty)
    }

    @Test func aFileThatIsNotUTF8IsNotOpenedForEditing() throws {
        let folder = try TempFolder()
        let file = folder.url.appending(path: "AGENTS.md")
        try Data([0xff, 0xfe, 0x00, 0xd8]).write(to: file)
        let doc = InstructionDocument(url: file)
        doc.load()
        guard case .failed(let message) = doc.state else {
            Issue.record("expected failed, got \(doc.state)")
            return
        }
        #expect(message.contains("UTF-8"))
    }

    @Test func savingKeepsTheFileMode() throws {
        let folder = try TempFolder()
        let file = try folder.write("AGENTS.md", "x\n")
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        let doc = InstructionDocument(url: file)
        doc.load()
        doc.text = "y\n"
        #expect(doc.save() == .saved)
        let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
    }
}
