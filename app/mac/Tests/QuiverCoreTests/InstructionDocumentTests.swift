import Foundation
import Testing
@testable import QuiverCore

@MainActor
@Suite struct InstructionDocumentTests {
    @Test func loadsAndSavesInPlace() async throws {
        let folder = try TempDir()
        let file = try folder.write("AGENTS.md", "# Rules\n")
        let doc = InstructionDocument(url: file)
        await doc.load()
        #expect(doc.state == .ready)
        #expect(doc.text == "# Rules\n")
        #expect(!doc.isDirty)

        doc.text += "Be brief.\n"
        #expect(doc.isDirty)
        #expect(await doc.save() == .saved)
        #expect(!doc.isDirty)
        #expect(try folder.read("AGENTS.md") == "# Rules\nBe brief.\n")
    }

    @Test func refusesToOverwriteAChangeMadeOnDiskSinceOpening() async throws {
        let folder = try TempDir()
        let file = try folder.write("AGENTS.md", "one\n")
        let doc = InstructionDocument(url: file)
        await doc.load()
        doc.text = "mine\n"
        _ = try folder.write("AGENTS.md", "theirs\n")

        #expect(await doc.save() == .changedOnDisk)
        #expect(doc.changedOnDisk)
        #expect(try folder.read("AGENTS.md") == "theirs\n")
        #expect(doc.text == "mine\n")

        #expect(await doc.save(overwrite: true) == .saved)
        #expect(!doc.changedOnDisk)
        #expect(try folder.read("AGENTS.md") == "mine\n")
    }

    @Test func aFileCreatedElsewhereWhileNewIsAlsoAConflict() async throws {
        let folder = try TempDir()
        let doc = InstructionDocument(url: folder.url.appending(path: "AGENTS.md"))
        await doc.load()
        #expect(doc.state == .ready)
        #expect(!doc.existsOnDisk)
        doc.text = "new\n"
        _ = try folder.write("AGENTS.md", "someone else's\n")
        #expect(await doc.save() == .changedOnDisk)
    }

    @Test func savingANewFileCreatesIt() async throws {
        let folder = try TempDir()
        let doc = InstructionDocument(url: folder.url.appending(path: "CLAUDE.md"))
        await doc.load()
        doc.text = "hello\n"
        #expect(await doc.save() == .saved)
        #expect(doc.existsOnDisk)
        #expect(try folder.read("CLAUDE.md") == "hello\n")
    }

    @Test func checkDiskPicksUpOutsideEditsWhenThereIsNothingToLose() async throws {
        let folder = try TempDir()
        let file = try folder.write("AGENTS.md", "v1\n")
        let doc = InstructionDocument(url: file)
        await doc.load()
        _ = try folder.write("AGENTS.md", "v2\n")
        await doc.checkDisk()
        #expect(doc.text == "v2\n")
        #expect(!doc.changedOnDisk)
    }

    @Test func checkDiskFlagsOutsideEditsWhenThereAreUnsavedOnes() async throws {
        let folder = try TempDir()
        let file = try folder.write("AGENTS.md", "v1\n")
        let doc = InstructionDocument(url: file)
        await doc.load()
        doc.text = "draft\n"
        _ = try folder.write("AGENTS.md", "v2\n")
        await doc.checkDisk()
        #expect(doc.changedOnDisk)
        #expect(doc.text == "draft\n")
    }

    @Test func savingThroughASymlinkWritesTheTargetAndKeepsTheLink() async throws {
        let folder = try TempDir()
        _ = try folder.write("AGENTS.md", "shared\n")
        try folder.link("CLAUDE.md", to: "AGENTS.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "CLAUDE.md"))
        await doc.load()
        #expect(doc.text == "shared\n")
        #expect(doc.linkTarget?.lastPathComponent == "AGENTS.md")

        doc.text = "edited\n"
        #expect(await doc.save() == .saved)
        #expect(folder.isLink("CLAUDE.md"))
        #expect(try folder.read("AGENTS.md") == "edited\n")
    }

    @Test func aDanglingLinkIsWrittenAtItsTargetNotReplaced() async throws {
        let folder = try TempDir()
        try folder.link("CLAUDE.md", to: "AGENTS.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "CLAUDE.md"))
        await doc.load()
        doc.text = "first\n"
        #expect(await doc.save() == .saved)
        #expect(folder.isLink("CLAUDE.md"))
        #expect(try folder.read("AGENTS.md") == "first\n")
    }

    @Test func aCircularLinkIsNeverReplacedByASave() async throws {
        let folder = try TempDir()
        try folder.link("A.md", to: "B.md")
        try folder.link("B.md", to: "A.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "A.md"))
        await doc.load()
        guard case .failed(let message) = doc.state else {
            Issue.record("expected failed, got \(doc.state)")
            return
        }
        #expect(message.contains("link"))
        doc.text = "x"
        guard case .failed = await doc.save(overwrite: true) else { Issue.record("save went through"); return }
        #expect(folder.isLink("A.md") && folder.isLink("B.md"))
    }

    @Test func checkDiskRetriesAFileThatFailedOnceItIsRepaired() async throws {
        let folder = try TempDir()
        try folder.link("A.md", to: "B.md")
        try folder.link("B.md", to: "A.md")
        let doc = InstructionDocument(url: folder.url.appending(path: "A.md"))
        await doc.load()
        guard case .failed = doc.state else { Issue.record("expected failed"); return }
        try FileManager.default.removeItem(at: folder.url.appending(path: "B.md"))
        _ = try folder.write("B.md", "fixed\n")
        await doc.checkDisk()
        #expect(doc.state == .ready)
        #expect(doc.text == "fixed\n")
    }

    @Test func revertDropsEdits() async throws {
        let folder = try TempDir()
        let doc = InstructionDocument(url: try folder.write("AGENTS.md", "keep\n"))
        await doc.load()
        doc.text = "oops"
        await doc.revert()
        #expect(doc.text == "keep\n")
        #expect(!doc.isDirty)
    }

    /// Reverting after an outside change drops the edits and loads the
    /// change, rather than leaving the conflict flag up over stale text.
    @Test func revertAfterAnOutsideChangeLoadsIt() async throws {
        let folder = try TempDir()
        let file = try folder.write("AGENTS.md", "old\n")
        let doc = InstructionDocument(url: file)
        await doc.load()
        doc.text = "mine"
        try "theirs\n".write(to: file, atomically: true, encoding: .utf8)
        await doc.checkDisk()
        #expect(doc.changedOnDisk)
        await doc.revert()
        #expect(!doc.changedOnDisk)
        #expect(doc.text == "theirs\n")
        #expect(!doc.isDirty)
    }

    /// A document that was never read (still loading, or a failed read)
    /// refuses to save, even with overwrite: its text isn't the file.
    @Test func aDocumentThatWasNotReadRefusesToSave() async throws {
        let folder = try TempDir()
        let file = folder.url.appending(path: "AGENTS.md")
        let bytes = Data([0xff, 0xfe, 0x00, 0xd8])
        try bytes.write(to: file)
        let doc = InstructionDocument(url: file)
        await doc.load()
        guard case .failed = await doc.save(overwrite: true) else {
            Issue.record("a failed read must not save"); return
        }
        #expect(try Data(contentsOf: file) == bytes)
        let fresh = InstructionDocument(url: folder.url.appending(path: "NEW.md"))
        guard case .failed = await fresh.save() else {
            Issue.record("a document still loading must not save"); return
        }
        #expect(!FileManager.default.fileExists(atPath: folder.url.appending(path: "NEW.md").path))
    }

    /// A second save waits for the first and writes on top of it with no
    /// false conflict; text typed while the first ran stays unsaved.
    @Test(.timeLimit(.minutes(1))) func overlappingSavesQueueAndKeepNewerTyping() async throws {
        let folder = try TempDir()
        let file = try folder.write("AGENTS.md", "v0\n")
        let doc = InstructionDocument(url: file)
        await doc.load()
        let gate = AsyncStream<Void>.makeStream()
        let held = AsyncStream<Void>.makeStream()
        var first = true
        doc.beforeWrite = {
            guard first else { return }
            first = false
            held.continuation.yield()
            for await _ in gate.stream { break }
        }
        doc.text = "one\n"
        let a = Task { await doc.save() }
        for await _ in held.stream { break }
        doc.text = "two\n"
        // b runs on the main actor, so once it says it started, the test
        // cannot resume until b's save has suspended: queued behind a.
        let started = AsyncStream<Void>.makeStream()
        let b = Task {
            started.continuation.yield()
            return await doc.save()
        }
        for await _ in started.stream { break }
        gate.continuation.yield()
        #expect(await a.value == .saved)
        #expect(await b.value == .saved)
        #expect(try String(contentsOf: file, encoding: .utf8) == "two\n")
        #expect(!doc.isDirty)
        #expect(!doc.changedOnDisk)

        // Typing during a save that nothing follows stays dirty.
        first = true
        let gate2 = AsyncStream<Void>.makeStream()
        let held2 = AsyncStream<Void>.makeStream()
        doc.beforeWrite = {
            held2.continuation.yield()
            for await _ in gate2.stream { break }
        }
        doc.text = "three\n"
        let c = Task { await doc.save() }
        for await _ in held2.stream { break }
        doc.text = "four\n"
        gate2.continuation.yield()
        #expect(await c.value == .saved)
        #expect(try String(contentsOf: file, encoding: .utf8) == "three\n")
        #expect(doc.isDirty)
        #expect(doc.text == "four\n")
    }

    @Test func aFileThatIsNotUTF8IsNotOpenedForEditing() async throws {
        let folder = try TempDir()
        let file = folder.url.appending(path: "AGENTS.md")
        try Data([0xff, 0xfe, 0x00, 0xd8]).write(to: file)
        let doc = InstructionDocument(url: file)
        await doc.load()
        guard case .failed(let message) = doc.state else {
            Issue.record("expected failed, got \(doc.state)")
            return
        }
        #expect(message.contains("UTF-8"))
    }

    @Test func savingKeepsTheFileMode() async throws {
        let folder = try TempDir()
        let file = try folder.write("AGENTS.md", "x\n")
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        let doc = InstructionDocument(url: file)
        await doc.load()
        doc.text = "y\n"
        #expect(await doc.save() == .saved)
        let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
    }
}
