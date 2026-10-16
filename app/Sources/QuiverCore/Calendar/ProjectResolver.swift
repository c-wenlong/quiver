import Foundation
import os

/// A working folder's project identity, memoized: `SessionProjects`
/// stats the disk for `.git` markers, so each cwd pays that once and the
/// answer is shared across providers. Thread-safe; `gitDir` is injectable
/// so tests never touch the real filesystem.
public final class ProjectResolver: Sendable {
    /// The group a block with no working folder lands in.
    public static let noFolder = "No folder"

    /// A project's identity and display name: `key` is the repository's
    /// root path so same-named folders of different repos stay apart,
    /// `name` is what the UI shows. No folder means an empty key and
    /// `noFolder` for display.
    public typealias Identity = (key: String, name: String)

    private let gitDir: @Sendable (String) -> SessionProjects.GitDir?
    private let cache = OSAllocatedUnfairLock<[String: Identity]>(initialState: [:])

    public init(gitDir: @escaping @Sendable (String) -> SessionProjects.GitDir?
                = { SessionProjects.gitDir(at: $0) }) {
        self.gitDir = gitDir
    }

    public func identity(of cwd: String?) -> Identity {
        guard let cwd, !cwd.isEmpty, cwd != "/" else {
            return ("", Self.noFolder)
        }
        if let cached = cache.withLock({ $0[cwd] }) { return cached }
        let found = resolve(cwd)
        cache.withLock { $0[cwd] = found }
        return found
    }

    /// The display name, for callers that group elsewhere.
    public func project(of cwd: String?) -> String { identity(of: cwd).name }

    private func resolve(_ cwd: String) -> Identity {
        if let root = SessionProjects.root(of: cwd, gitDir: gitDir) {
            return (root, SessionProjects.lastComponentOf(root) ?? Self.noFolder)
        }
        // No live `.git`: a worktree-shaped path still names its repo
        // root; any other folder is its own identity.
        let name = SessionProjects.project(of: cwd, gitDir: gitDir) ?? Self.noFolder
        return (SessionProjects.patternRoot(of: cwd) ?? cwd, name)
    }

    /// A provider's per-session blocks re-keyed into project blocks.
    public func group(_ blocks: [ActivityBlock], gap: TimeInterval) -> [ActivityBlock] {
        ActivityBlocks.groupByProject(blocks, gap: gap) { identity(of: $0) }
    }
}
