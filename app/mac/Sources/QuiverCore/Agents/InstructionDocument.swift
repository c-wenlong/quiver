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
        writeURL = url
    }

    public var isDirty: Bool { text != savedText }

    /// Where a save writes: the end of the symlink chain at `url`, as the
    /// last read or write found it (`url` itself before the first read).
    public private(set) var writeURL: URL

    /// The target, when `url` is a symlink; for saying where edits land.
    public var linkTarget: URL? {
        writeURL.standardizedFileURL == url.standardizedFileURL ? nil : writeURL
    }

    /// The save in flight, so a second save waits for the first and
    /// compares against what it wrote rather than reporting a conflict.
    private var saving: Task<SaveOutcome, Never>?
    /// Test seam: awaited just before a save's disk write, so a test can
    /// hold a save in flight.
    var beforeWrite: (@MainActor () async -> Void)?

    /// Reads the file, replacing any edits. The read (and the symlink walk
    /// before it) runs off the main actor: a file under a synced or stale
    /// folder can block `stat` or `read` for as long as it likes.
    public func load() async {
        let url = self.url
        // The editor stays open while a reload reads; typing in that window
        // must not be thrown away by the text the read brings back.
        let typed = text
        let (target, result) = await offMain { () -> (URL, Result<Data?, Error>) in
            let target = Self.resolveLinks(url)
            return (target, Self.read(target))
        }
        writeURL = target
        if text != typed, state == .ready {
            // Edited while reading: keep the edits, and flag the file when
            // it no longer holds what they were made against.
            if case .success(let data) = result, data != baseline { changedOnDisk = true }
            return
        }
        switch result {
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

    /// Throws away unsaved edits. When the file changed on disk meanwhile,
    /// that change is what comes back: the edits were the only reason not
    /// to load it, and keeping the stale text would leave the conflict
    /// flag up with nothing left to conflict.
    public func revert() async {
        if changedOnDisk {
            await load()
        } else {
            text = savedText
        }
    }

    /// Looks at the disk again: picks up an outside change when there are no
    /// edits to lose, and flags it when there are.
    public func checkDisk() async {
        if case .failed = state, !isDirty {
            // Whatever stopped the last read (a broken link, a permission)
            // may be fixed by now.
            await load()
            return
        }
        guard state == .ready, saving == nil else { return }
        let url = self.url
        let known = baseline
        guard case .success(let current) = await offMain({ Self.read(Self.resolveLinks(url)) }),
              current != known,
              // A save or load that landed meanwhile already knows the file.
              baseline == known, saving == nil
        else { return }
        if isDirty {
            changedOnDisk = true
        } else {
            await load()
        }
    }

    /// Writes `text`, unless the file changed on disk since it was read and
    /// `overwrite` is false. The read-compare-write runs off the main actor
    /// as one step; text typed while it runs stays as unsaved edits.
    @discardableResult
    public func save(overwrite: Bool = false) async -> SaveOutcome {
        while let running = saving {
            _ = await running.value
            // A finished save's own frame may not have run yet to clear
            // the slot; clear it here so this loop can't spin on it.
            if saving == running { saving = nil }
        }
        // Only a file that was read can be written: while loading, or after
        // a failed read (not UTF-8, a broken link), `text` is not the file,
        // and an overwrite would replace the file with it.
        guard state == .ready else {
            return .failed("\(url.lastPathComponent) hasn't been read, so it can't be saved.")
        }
        let url = self.url
        let edited = text
        let known = baseline
        let task = Task { () -> SaveOutcome in
            await beforeWrite?()
            let (target, written) = await offMain { () -> (URL, Result<Data, SaveFailure>) in
                let target = Self.resolveLinks(url)
                return (target, Self.write(edited, to: target, ifStill: overwrite ? nil : .some(known)))
            }
            writeURL = target
            switch written {
            case .success(let data):
                savedText = edited
                baseline = data
                existsOnDisk = true
                changedOnDisk = false
                state = .ready
                return .saved
            case .failure(.changedOnDisk):
                changedOnDisk = true
                return .changedOnDisk
            case .failure(.failed(let message)):
                return .failed(message)
            }
        }
        saving = task
        let outcome = await task.value
        if saving == task { saving = nil }
        return outcome
    }

    private enum SaveFailure: Error {
        case changedOnDisk
        case failed(String)
    }

    /// `text` written atomically at `target`, keeping the file's mode.
    /// `ifStill` (when not nil) is the bytes the file must still hold.
    private nonisolated static func write(_ text: String, to target: URL,
                                          ifStill expected: Data??) -> Result<Data, SaveFailure> {
        let current: Data?
        switch read(target) {
        case .success(let data): current = data
        case .failure(let error):
            return .failure(.failed("Couldn't read \(target.path): \(error.localizedDescription)"))
        }
        if let expected, current != expected { return .failure(.changedOnDisk) }
        let data = Data(text.utf8)
        do {
            // An atomic write replaces the file, which would reset its mode.
            let mode = try? FileManager.default.attributesOfItem(atPath: target.path)[.posixPermissions]
            try data.write(to: target, options: .atomic)
            if let mode {
                try? FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: target.path)
            }
        } catch {
            return .failure(.failed("Couldn't save \(target.path): \(error.localizedDescription)"))
        }
        return .success(data)
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
