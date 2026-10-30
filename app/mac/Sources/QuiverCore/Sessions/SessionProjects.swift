import Foundation

/// The repository a session's working folder belongs to, so a worktree's
/// sessions count under the main repository's name. `project(of:)` walks
/// up to the nearest `.git` and reads it when it is a worktree's file;
/// paths that no longer exist fall back to the worktree shapes swe and
/// the harnesses use, matched by pattern. Every answer is per folder, so
/// the caller caches them.
public enum SessionProjects {
    /// What a folder's `.git` is: the repository's own directory, or the
    /// file a linked worktree writes, naming the real gitdir.
    public enum GitDir: Equatable, Sendable {
        case repository
        /// The `.git` file's contents (the `gitdir: ...` line).
        case worktree(String)
    }

    /// `folder/.git` on disk, injectable for tests.
    public static func gitDir(at folder: String, fileManager: FileManager = .default) -> GitDir? {
        var isDir: ObjCBool = false
        let git = (folder as NSString).appendingPathComponent(".git")
        guard fileManager.fileExists(atPath: git, isDirectory: &isDir) else { return nil }
        if isDir.boolValue { return .repository }
        guard let text = try? String(contentsOfFile: git, encoding: .utf8) else { return .repository }
        return .worktree(text)
    }

    /// The project `cwd` belongs to: the nearest enclosing repository's
    /// folder name, following a worktree's `.git` file back to the main
    /// repository. `gitDir` answers what `folder/.git` holds so tests
    /// don't touch the disk.
    public static func project(of cwd: String,
                               gitDir: (String) -> GitDir? = { gitDir(at: $0) }) -> String? {
        if let root = root(of: cwd, gitDir: gitDir) { return lastComponent(root) }
        return pattern(of: cwd) ?? lastComponent(cwd)
    }

    /// The repository root `cwd` sits inside: the folder holding the
    /// nearest `.git`, or for a worktree the main repository's folder,
    /// so two worktrees of one repo share it and same-named folders of
    /// different repos stay apart. Nil when no `.git` marker exists —
    /// `pattern(of:)` names are display fallbacks, not identity.
    public static func root(of cwd: String,
                            gitDir: (String) -> GitDir? = { gitDir(at: $0) }) -> String? {
        guard !cwd.isEmpty else { return nil }
        var folder = normalized(cwd)
        while !folder.isEmpty, folder != "/" {
            if let marker = gitDir(folder) {
                switch marker {
                case .repository:
                    return folder
                case .worktree(let contents):
                    return mainRepoRoot(of: contents, beside: folder) ?? folder
                }
            }
            folder = (folder as NSString).deletingLastPathComponent
        }
        return nil
    }

    /// The repository a worktree's `.git` file points at: its `gitdir:`
    /// line, relative or absolute, resolved against the worktree's own
    /// folder, then the folder above its `/.git` part. The root path, not
    /// the name: the identity project grouping keys on.
    public static func mainRepoRoot(of contents: String, beside folder: String) -> String? {
        guard let line = contents.split(separator: "\n").first(where: { $0.hasPrefix("gitdir:") }) else {
            return nil
        }
        var path = String(line.dropFirst("gitdir:".count)).trimmingCharacters(in: .whitespaces)
        if !path.hasPrefix("/") {
            path = (folder as NSString).appendingPathComponent(path)
        }
        path = (path as NSString).standardizingPath
        if let marker = path.range(of: "/.git/") {
            return String(path[..<marker.lowerBound])
        }
        if path.hasSuffix("/.git") {
            return String(path.dropLast("/.git".count))
        }
        return nil
    }

    /// The repository a worktree path belonged to when the path itself is
    /// gone, matched by the shapes swe and the harnesses keep worktrees
    /// under: `<repo>/.claude/worktrees/<name>`, `<repo>/worktrees/<name>`
    /// (or `.worktrees`), and `~/.codex/worktrees/<hash>/<repo>`.
    public static func pattern(of cwd: String) -> String? {
        let parts = cwd.split(separator: "/").map(String.init)
        for (index, part) in parts.enumerated() where index > 0 {
            switch part {
            case ".claude" where parts.indices.contains(index + 1) && parts[index + 1] == "worktrees":
                return parts[index - 1]
            case ".codex" where parts.indices.contains(index + 1) && parts[index + 1] == "worktrees":
                // The hash sits between "worktrees" and the repo name.
                return parts.indices.contains(index + 3) ? parts[index + 3] : nil
            case ".worktrees", "worktrees":
                return parts[index - 1]
            default:
                continue
            }
        }
        return nil
    }

    /// The repository root a gone worktree path implies, for the shapes
    /// `pattern(of:)` names: the path up to the component before the
    /// marker. `~/.codex/worktrees/<hash>/<repo>` has no real root, so it
    /// answers nil.
    public static func patternRoot(of cwd: String) -> String? {
        let parts = cwd.split(separator: "/").map(String.init)
        for (index, part) in parts.enumerated() where index > 0 {
            switch part {
            case ".codex" where parts.indices.contains(index + 1) && parts[index + 1] == "worktrees":
                // `~/.codex/worktrees/<id>/<name>` holds unrelated repos;
                // the only root visible is ~/.codex itself, which would
                // merge them all, so there is none.
                return nil
            case ".claude" where parts.indices.contains(index + 1) && parts[index + 1] == "worktrees",
                 ".worktrees", "worktrees":
                return "/" + parts[..<index].joined(separator: "/")
            default:
                continue
            }
        }
        return nil
    }

    /// A folder name that is a generated id rather than a project: hex of
    /// 7 or more characters, a UUID, or a name that is mostly hex and
    /// digits, like a worktree hash. A UUID needs no rule of its own: its
    /// 32 letters and digits are all hex. No regex, since the Sessions
    /// page asks this for every row.
    public static func isHashLike(_ name: String) -> Bool {
        let word = name.filter { $0.isLetter || $0.isNumber }
        guard word.count >= 7 else { return false }
        let hex = word.count(where: \.isHexDigit)
        return Double(hex) >= Double(word.count) * 0.8
    }

    private static func normalized(_ path: String) -> String {
        path.hasSuffix("/") && path.count > 1 ? String(path.dropLast()) : path
    }

    static func lastComponentOf(_ path: String) -> String? {
        lastComponent(path)
    }

    private static func lastComponent(_ path: String) -> String? {
        path.split(separator: "/").last.map(String.init)
    }
}
