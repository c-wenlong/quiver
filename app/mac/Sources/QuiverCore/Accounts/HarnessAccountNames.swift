import Foundation

/// Account names for harness cards (the "Cortex" you gave Cursor), owned by
/// the app and never by swe: a rename in Accounts must not rewrite
/// harness.json, which the Harnesses page's own names live in. Next to
/// profiles.json at `~/.quiver/accounts/harness-names.json`.
public final class HarnessAccountNameStore: @unchecked Sendable {
    public static var defaultFile: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appending(path: ".quiver/accounts/harness-names.json")
    }

    private let file: URL
    private let lock = NSLock()

    public init(file: URL) {
        self.file = file
        PrivateFile.tighten(file)
    }

    private struct Document: Codable {
        var schema: Int
        var names: [String: String]
    }

    /// The names on disk: an absent or unreadable file reads as none.
    public func load() -> [String: String] {
        guard let data = try? Data(contentsOf: file),
              let doc = try? JSONDecoder().decode(Document.self, from: data),
              doc.schema == 1
        else { return [:] }
        return doc.names
    }

    /// Set or clear (nil/empty) one harness's account name. Throws rather
    /// than overwrite a file that is present but unreadable — better to lose
    /// one rename than to flatten a document we could not parse.
    public func setName(_ name: String?, for harness: String) throws {
        lock.lock()
        defer { lock.unlock() }
        var names: [String: String] = [:]
        let existing: Data?
        do {
            existing = try Data(contentsOf: file)
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile
                || error.code == .fileNoSuchFile {
            existing = nil
        }
        if let existing {
            guard let doc = try? JSONDecoder().decode(Document.self, from: existing),
                  doc.schema == 1
            else { throw CocoaError(.coderReadCorrupt) }
            names = doc.names
        }
        let trimmed = name?.trimmingCharacters(in: .whitespaces) ?? ""
        if trimmed.isEmpty {
            names.removeValue(forKey: harness)
        } else {
            names[harness] = trimmed
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(Document(schema: 1, names: names))
        try PrivateFile.write(data, to: file)
    }
}
