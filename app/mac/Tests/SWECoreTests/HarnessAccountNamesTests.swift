import Foundation
import Testing
@testable import SWECore

@Suite struct HarnessAccountNameStoreTests {
    /// This test's own folder, removed with its contents when the test ends.
    let scratch: TempDir
    init() throws { scratch = try TempDir() }

    private func tempFile() -> URL {
        scratch.url
            .appendingPathComponent("quiver-names-\(UUID().uuidString)")
            .appendingPathComponent("harness-names.json")
    }

    @Test func namesRoundTrip() throws {
        let file = tempFile()
        let store = HarnessAccountNameStore(file: file)
        try store.setName("Cortex", for: "cursor")
        try store.setName("For work", for: "devin")
        #expect(store.load() == ["cursor": "Cortex", "devin": "For work"])
        // A fresh store on the same file reads it back.
        #expect(HarnessAccountNameStore(file: file).load()["cursor"] == "Cortex")
    }

    @Test func emptyAndNilClearTheName() throws {
        let file = tempFile()
        let store = HarnessAccountNameStore(file: file)
        try store.setName("Cortex", for: "cursor")
        try store.setName("   ", for: "cursor")
        #expect(store.load() == [:])
        try store.setName("Cortex", for: "cursor")
        try store.setName(nil, for: "cursor")
        #expect(store.load() == [:])
    }

    @Test func aMissingFileReadsAsEmpty() {
        #expect(HarnessAccountNameStore(file: tempFile()).load() == [:])
    }

    @Test func aCorruptFileReadsEmptyAndRefusesToWrite() throws {
        let file = tempFile()
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let garbage = Data("not json at all".utf8)
        try garbage.write(to: file)
        let store = HarnessAccountNameStore(file: file)
        #expect(store.load() == [:])
        #expect(throws: (any Error).self) { try store.setName("Cortex", for: "cursor") }
        // Nothing was overwritten.
        #expect(try Data(contentsOf: file) == garbage)
    }

    @Test(skippedAsRoot) func anUnreadableFileThrowsInsteadOfBeingFlattened() throws {
        let file = tempFile()
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(#"{"schema": 1, "names": {"cursor": "Cortex"}}"#.utf8).write(to: file)
        let bytes = try Data(contentsOf: file)
        try FileManager.default.setAttributes([.posixPermissions: 0], ofItemAtPath: file.path)
        defer {
            try? FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: file.path)
        }
        let store = HarnessAccountNameStore(file: file)
        // A read error that isn't "missing" must not become an empty write.
        #expect(throws: (any Error).self) { try store.setName("New", for: "cursor") }
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: file.path)
        #expect(try Data(contentsOf: file) == bytes)
    }

    @Test func anUnknownSchemaReadsEmptyAndRefusesToWrite() throws {
        let file = tempFile()
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let future = Data(#"{"schema": 2, "names": {"cursor": "Cortex"}}"#.utf8)
        try future.write(to: file)
        let store = HarnessAccountNameStore(file: file)
        #expect(store.load() == [:])
        #expect(throws: (any Error).self) { try store.setName("X", for: "cursor") }
        #expect(try Data(contentsOf: file) == future)
    }
}
