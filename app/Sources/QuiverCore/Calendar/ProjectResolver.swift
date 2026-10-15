import Foundation
import os

/// A working folder's project name, memoized: `SessionProjects.project(of:)`
/// stats the disk for `.git` markers, so each cwd pays that once and the
/// answer is shared across providers. Thread-safe; `gitDir` is injectable
/// so tests never touch the real filesystem.
public final class ProjectResolver: Sendable {
    /// The group a block with no working folder lands in.
    public static let noFolder = "No folder"

    private let gitDir: @Sendable (String) -> SessionProjects.GitDir?
    private let cache = OSAllocatedUnfairLock<[String: String]>(initialState: [:])

    public init(gitDir: @escaping @Sendable (String) -> SessionProjects.GitDir?
                = { SessionProjects.gitDir(at: $0) }) {
        self.gitDir = gitDir
    }

    public func project(of cwd: String?) -> String {
        guard let cwd, !cwd.isEmpty else { return Self.noFolder }
        if let cached = cache.withLock({ $0[cwd] }) { return cached }
        let name = SessionProjects.project(of: cwd, gitDir: gitDir) ?? Self.noFolder
        cache.withLock { $0[cwd] = name }
        return name
    }

    /// A provider's per-session blocks re-keyed into project blocks.
    public func group(_ blocks: [ActivityBlock], gap: TimeInterval) -> [ActivityBlock] {
        ActivityBlocks.groupByProject(blocks, gap: gap) { project(of: $0) }
    }
}
