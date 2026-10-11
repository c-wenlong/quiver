import Foundation
import Observation

/// One instruction file (AGENTS.md, CLAUDE.md) open for editing.
///
/// Remembers the bytes it read, so a save can tell whether something else
/// changed the file since: an agent rewriting its own AGENTS.md mid-session
/// is ordinary here, and a save must not quietly throw that away. A file
/// reached through a symlink is written at the link's target, so the link
/// (the thing `swe init` made) survives the save.
@MainActor
@Observable
public final class InstructionDocument: Identifiable {
    public enum State: Equatable, Sendable {
        case loading
        case ready
        case failed(String)
    }

    public enum SaveOutcome: Equatable, Sendable {
        case saved
        /// The file on disk is no longer what was opened; nothing was written.
        case changedOnDisk
        case failed(String)
    }

    /// The path as the user knows it, which may be a symlink.
    public nonisolated let url: URL
    public nonisolated var id: URL { url }

    public var text = ""
    public private(set) var savedText = ""
    public private(set) var state: State = .loading
    /// Set when the file changed on disk while there were unsaved edits.
    public private(set) var changedOnDisk = false
    public private(set) var existsOnDisk = false
    /// Bytes on disk when last read or written; nil when there was no file.
    private var baseline: Data?

    public init(url: URL) {
        self.url = url
    }

    public var isDirty: Bool { text != savedText }

    /// Where a save writes: the end of the symlink chain at `url`.
    public var writeURL: URL { Self.resolveLinks(url) }

    /// The target, when `url` is a symlink; for saying where edits land.
    public var linkTarget: URL? {
        let target = writeURL
        return target.standardizedFileURL == url.standardizedFileURL ? nil : target
    }

    /// Reads the file, replacing any edits.
    public func load() {
        switch Self.read(writeURL) {
        case .success(let data):
            guard let data else {
                apply(text: "", baseline: nil)
                return
            }
            guard let text = String(data: data, encoding: .utf8) else {
                state = .failed("\(url.lastPathComponent) isn't UTF-8 text, so it can't be edited here.")
                return
            }
            apply(text: text, baseline: data)
        case .failure(let error):
            state = .failed("Couldn't read \(url.path): \(error.localizedDescription)")
        }
    }

    /// Throws away unsaved edits.
    public func revert() {
        text = savedText
    }

    /// Looks at the disk again: picks up an outside change when there are no
    /// edits to lose, and flags it when there are.
    public func checkDisk() {
        guard state == .ready, case .success(let current) = Self.read(writeURL),
              current != baseline else { return }
        if isDirty {
            changedOnDisk = true
        } else {
            load()
        }
    }

    /// Writes `text`, unless the file changed on disk since it was read and
    /// `overwrite` is false.
    @discardableResult
    public func save(overwrite: Bool = false) -> SaveOutcome {
        let target = writeURL
        let current: Data?
        switch Self.read(target) {
        case .success(let data): current = data
        case .failure(let error): return .failed("Couldn't read \(target.path): \(error.localizedDescription)")
        }
        if !overwrite && current != baseline {
            changedOnDisk = true
            return .changedOnDisk
        }
        let data = Data(text.utf8)
        do {
            // An atomic write replaces the file, which would reset its mode.
            let mode = try? FileManager.default.attributesOfItem(atPath: target.path)[.posixPermissions]
            try data.write(to: target, options: .atomic)
            if let mode {
                try? FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: target.path)
            }
        } catch {
            return .failed("Couldn't save \(target.path): \(error.localizedDescription)")
        }
        let edited = text
        apply(text: edited, baseline: data)
        return .saved
    }

    private func apply(text: String, baseline: Data?) {
        self.text = text
        savedText = text
        self.baseline = baseline
        existsOnDisk = baseline != nil
        changedOnDisk = false
        state = .ready
    }

    /// The file's bytes, nil when there is no file there.
    nonisolated static func read(_ url: URL) -> Result<Data?, Error> {
        if isLink(url) {
            // resolveLinks stopped on a link: a cycle or a very long chain.
            // Reading it as missing would let a save replace the link.
            return .failure(CocoaError(.fileReadUnknown, userInfo: [
                NSLocalizedDescriptionKey: "its symlink chain is circular or too long"]))
        }
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory) else {
            return .success(nil)
        }
        if isDirectory.boolValue {
            return .failure(CocoaError(.fileReadUnknown, userInfo: [NSLocalizedDescriptionKey: "it is a folder"]))
        }
        return Result { try Data(contentsOf: url) }
    }

    nonisolated static func isLink(_ url: URL) -> Bool {
        (try? FileManager.default.destinationOfSymbolicLink(atPath: url.path)) != nil
    }

    /// Follows symlinks at the last path component, including dangling ones,
    /// which `resolvingSymlinksInPath` would leave in place and an atomic
    /// write would then replace with a plain file.
    nonisolated static func resolveLinks(_ url: URL) -> URL {
        var current = url
        for _ in 0..<16 {
            guard let destination = try? FileManager.default.destinationOfSymbolicLink(atPath: current.path) else {
                return current
            }
            current = destination.hasPrefix("/")
                ? URL(fileURLWithPath: destination)
                : current.deletingLastPathComponent().appending(path: destination).standardizedFileURL
        }
        return current
    }
}
