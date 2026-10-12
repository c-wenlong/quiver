import Foundation
import Observation

/// A detail page's open file documents, one per path for the page's
/// life: picking another file or stepping away and back never drops an
/// unsaved edit.
@MainActor
@Observable
public final class PrimitiveDrafts {
    public private(set) var documents: [String: InstructionDocument] = [:]

    public init() {}

    /// The document for `path`, opened and loaded on first ask.
    @discardableResult
    public func document(for path: String) -> InstructionDocument {
        if let document = documents[path] { return document }
        let document = InstructionDocument(url: URL(fileURLWithPath: path))
        document.load()
        documents[path] = document
        return document
    }

    /// The paths with unsaved text.
    public var dirty: [String: InstructionDocument] {
        documents.filter { $0.value.isDirty }
    }

    /// Drop the document at `path` and every document under it, for a
    /// trashed file or folder: a later save-all must not write a file
    /// that no longer exists.
    public func remove(under path: String) {
        documents = documents.filter { key, _ in
            key != path && !key.hasPrefix(path + "/")
        }
    }
}
