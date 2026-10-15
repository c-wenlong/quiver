import Foundation
import Testing
@testable import QuiverCore

@Suite struct HarnessAccountNameStoreTests {
    private func tempFile() -> URL {
        FileManager.default.temporaryDirectory
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
