import Foundation

/// A fresh temp folder that removes itself when the last reference goes.
/// The same helper as `SWECoreTests/LedgerTestSupport.swift`; test
/// targets can't share sources.
final class TempDir: @unchecked Sendable {
    let url: URL

    init() throws {
        url = FileManager.default.temporaryDirectory
            .appendingPathComponent("quiver-app-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    deinit { try? FileManager.default.removeItem(at: url) }
}
