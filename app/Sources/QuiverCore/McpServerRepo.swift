import Foundation
import Observation

/// A server checkout inside the managed MCP servers folder.
public struct McpRepoServer: Equatable, Sendable, Identifiable {
    public var id: String { path.path }
    /// The folder's name.
    public let name: String
    /// The group folder holding it (`custom`, `official`), nil for a
    /// checkout right under the root.
    public let group: String?
    public let path: URL
    /// nil while the state is still being read.
    public var git: GitState?

    public init(name: String, group: String? = nil, path: URL, git: GitState? = nil) {
        self.name = name
        self.group = group
        self.path = path
        self.git = git
    }
}

/// `git status --porcelain=v1 --branch` boiled down to what a row shows.
public struct GitState: Equatable, Sendable {
    /// nil on a detached HEAD or outside a repository.
    public let branch: String?
    public let ahead: Int
    public let behind: Int
    /// Tracked or untracked files differ from HEAD.
    public let hasChanges: Bool
    public let isRepo: Bool

    public init(branch: String? = nil, ahead: Int = 0, behind: Int = 0,
                hasChanges: Bool = false, isRepo: Bool = true) {
        self.branch = branch
        self.ahead = ahead
        self.behind = behind
        self.hasChanges = hasChanges
        self.isRepo = isRepo
    }

    /// The first line names the branch and how far it is from its upstream;
    /// every line after it is a changed file.
    ///
    ///     ## main...origin/main [ahead 1, behind 2]
    ///     ## main                          no upstream
    ///     ## HEAD (no branch)              detached
    ///     ## No commits yet on main        fresh init
    public static func parse(porcelain: String) -> GitState {
        let lines = porcelain.split(separator: "\n")
        guard let first = lines.first, first.hasPrefix("## ") else {
            return GitState(isRepo: false)
        }
        var head = first.dropFirst(3)
        var ahead = 0
        var behind = 0
        if let open = head.range(of: " [", options: .backwards), head.hasSuffix("]") {
            for part in head[open.upperBound...].dropLast().split(separator: ",") {
                let part = part.trimmingCharacters(in: .whitespaces)
                if part.hasPrefix("ahead ") { ahead = Int(part.dropFirst(6)) ?? 0 }
                if part.hasPrefix("behind ") { behind = Int(part.dropFirst(7)) ?? 0 }
            }
            head = head[..<open.lowerBound]
        }
        let hasChanges = lines.count > 1
        if head.hasPrefix("HEAD (no branch)") {
            return GitState(ahead: ahead, behind: behind, hasChanges: hasChanges)
        }
        if head.hasPrefix("No commits yet on ") {
            return GitState(branch: String(head.dropFirst(18)),
                            ahead: ahead, behind: behind, hasChanges: hasChanges)
        }
        // "main...origin/main": the branch name ends at the upstream, and a
        // ref name can never hold "..", so the three dots are safe to cut on.
        if let dots = head.range(of: "...") { head = head[..<dots.lowerBound] }
        return GitState(branch: String(head), ahead: ahead, behind: behind,
                        hasChanges: hasChanges)
    }
}

/// Why the servers folder could not be scanned.
public enum McpRepoError: Error, LocalizedError, Equatable {
    /// The path points at a plain file.
    case notAFolder(String)
    /// The folder exists but its listing failed; the reason goes with it.
    case unreadable(String, String)

    public var errorDescription: String? {
        switch self {
        case .notAFolder(let path): "\(path) is a file, not a folder."
        case .unreadable(let path, let reason): "Couldn't read \(path): \(reason)."
        }
    }
}

/// Reads the managed servers folder: which checkouts are there, which hub
/// servers point at them, and their git state.
public enum McpServerRepo {
    /// A folder holding one of these is a server itself, not a group folder.
    static let serverMarkers = [".git", "package.json", "pyproject.toml", "go.mod",
                                "Cargo.toml", "Dockerfile", "setup.py", "requirements.txt",
                                "server.py", "main.py", "index.js"]

    /// The servers under `root`: a direct child with no marker is a group
    /// folder whose children are the servers, and any other child is a
    /// server on its own. Hidden folders and plain files never count, and
    /// the listing stops there. A missing root is an empty list; a root
    /// that is a file or can't be read throws instead of looking empty.
    public static func scan(root: URL, fileManager: FileManager = .default) throws -> [McpRepoServer] {
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: root.path, isDirectory: &isDirectory) else { return [] }
        guard isDirectory.boolValue else { throw McpRepoError.notAFolder(root.path) }
        let children: [URL]
        do {
            children = try folders(in: root, fileManager: fileManager)
        } catch {
            throw McpRepoError.unreadable(root.path, error.localizedDescription)
        }
        var servers: [McpRepoServer] = []
        for child in children {
            if isGroup(child, fileManager: fileManager) {
                // A group folder that can't be read just shows no servers.
                for member in (try? folders(in: child, fileManager: fileManager)) ?? [] {
                    servers.append(McpRepoServer(name: member.lastPathComponent,
                                                 group: child.lastPathComponent, path: member))
                }
            } else {
                servers.append(McpRepoServer(name: child.lastPathComponent, path: child))
            }
        }
        // Ungrouped first, then groups alphabetically, names inside them.
        return servers.sorted { lhs, rhs in
            if lhs.group != rhs.group {
                guard let left = lhs.group else { return true }
                guard let right = rhs.group else { return false }
                let order = left.localizedCaseInsensitiveCompare(right)
                if order != .orderedSame { return order == .orderedAscending }
            }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    /// A child counts as a group when nothing marks it as a server itself.
    private static func isGroup(_ url: URL, fileManager: FileManager) -> Bool {
        !serverMarkers.contains { fileManager.fileExists(atPath: url.appending(path: $0).path) }
    }

    /// The non-hidden folders right inside `url`.
    private static func folders(in url: URL, fileManager: FileManager) throws -> [URL] {
        try fileManager.contentsOfDirectory(
            at: url, includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles])
            .filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
    }

    /// Each server folder (standardized path) -> the hub servers that run
    /// from inside it. A hub command or argument counts when it is an
    /// absolute path that is the folder or sits under it, so
    /// `node …/linear-mcp/build/index.js` and
    /// `uv --directory …/fathom-mcp run …` match while `mcp-hnx` never
    /// answers for `mcp-hn`.
    public static func hubUsage(servers: [McpRepoServer], hub: [McpServer]) -> [String: [String]] {
        var usage: [String: [String]] = [:]
        for server in servers {
            let base = server.path.standardizedFileURL.path
            for hubServer in hub {
                var words = hubServer.args
                if let command = hubServer.command {
                    // A bare executable path can hold spaces, so the whole
                    // string is a candidate; a command line also gets split
                    // quote-aware, so `node "/srv/my server/index.js"` and
                    // `node /srv/my\ server/index.js` still match. The words
                    // are unquoted the way exec receives them.
                    words.append(command)
                    words += McpRedaction.shellWords(command).map(unquote)
                }
                if words.contains(where: { inside($0, folder: base) }) {
                    usage[base, default: []].append(hubServer.name)
                }
            }
        }
        return usage
    }

    /// `word` as a path, exactly as given: `file://` decoded, relative
    /// words ignored. Quotes and backslashes are kept — a literal arg or a
    /// bare command may legitimately contain them, and command-line words
    /// arrive already unquoted.
    private static func inside(_ word: String, folder base: String) -> Bool {
        var word = word
        if word.hasPrefix("file://") {
            guard let decoded = URL(string: word)?.path(percentEncoded: false) else { return false }
            word = decoded
        }
        guard word.hasPrefix("/") else { return false }
        let path = URL(fileURLWithPath: word).standardizedFileURL.path
        return path == base || path.hasPrefix(base + "/")
    }

    /// A word the way exec receives it: quotes removed and backslash
    /// escapes undone, since shellWords keeps both in its output. Inside
    /// single quotes a backslash is literal.
    private static func unquote(_ word: String) -> String {
        var out = ""
        var quote: Character?
        var escaped = false
        for character in word {
            if escaped {
                out.append(character)
                escaped = false
            } else if character == "\\", quote != "'" {
                escaped = true
            } else if let open = quote {
                if character == open { quote = nil } else { out.append(character) }
            } else if character == "\"" || character == "'" {
                quote = character
            } else {
                out.append(character)
            }
        }
        if escaped { out.append("\\") }
        return out
    }

    /// The folder's git state, reading stdout to EOF so a long status can't
    /// stall the pipe, with five seconds before the process is let go.
    /// Anything off (not a repo, git missing, a timeout) is `isRepo: false`.
    public static func gitState(of url: URL) async -> GitState {
        await runOffPool { Self.gitStateSync(of: url) }
    }

    private static func gitStateSync(of url: URL) -> GitState {
        // Without its own .git (a directory, or a file for worktrees and
        // submodules), `git status` would report the enclosing repository's
        // state — e.g. quiver-hub's, for folders under ~/.quiver.
        guard FileManager.default.fileExists(atPath: url.appending(path: ".git").path) else {
            return GitState(isRepo: false)
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/git")
        process.arguments = ["-C", url.path, "status", "--porcelain=v1", "--branch"]
        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        do { try process.run() } catch { return GitState(isRepo: false) }
        let deadline = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + 5, execute: deadline)
        let out = output.fileHandleForReading.readDataToEndOfFile()
        // status's stderr is one fatal line at most, so reading it after
        // stdout can never deadlock the pipe.
        let err = errors.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        deadline.cancel()
        guard process.terminationStatus == 0,
              !String(decoding: err, as: UTF8.self).contains("not a git repository") else {
            return GitState(isRepo: false)
        }
        return GitState.parse(porcelain: String(decoding: out, as: UTF8.self))
    }
}

/// The MCP servers page's "Server folders" section.
@MainActor
@Observable
public final class McpServerRepoModel {
    /// Where the checkouts live, set from the Paths setting.
    public var root: URL
    public private(set) var servers: [McpRepoServer] = []
    public private(set) var isLoading = false
    public private(set) var error: String?
    private var ticket = 0

    /// Git checks run at most this many at a time.
    static let gitBatch = 4

    public init(root: URL = FileManager.default.homeDirectoryForCurrentUser
        .appending(path: ".quiver/mcp/servers")) {
        self.root = root
    }

    /// Reads the folder again: names and groups first, then each checkout's
    /// git state (four at a time), published as each lands. Which hub
    /// servers run from a folder is computed by the view from the current
    /// inventory, so it is never stale. A ticket keeps an older read from
    /// overwriting a newer one.
    public func load() async {
        ticket += 1
        let mine = ticket
        isLoading = true
        error = nil
        let root = self.root
        let outcome = await runOffPool { Result { try McpServerRepo.scan(root: root) } }
        let found: [McpRepoServer]
        do {
            found = try outcome.get()
        } catch {
            if mine == ticket {
                self.error = error.localizedDescription
                isLoading = false
            }
            return
        }
        guard mine == ticket else { return }
        servers = found
        var pending = found.indices.makeIterator()
        await withTaskGroup(of: (Int, GitState).self) { group in
            var inFlight = 0
            // The count is checked before the index is taken, so a full
            // batch never swallows a folder.
            while inFlight < Self.gitBatch, let index = pending.next() {
                let path = found[index].path
                group.addTask { (index, await McpServerRepo.gitState(of: path)) }
                inFlight += 1
            }
            while let (index, git) = await group.next() {
                guard mine == ticket else { group.cancelAll(); return }
                servers[index].git = git
                inFlight -= 1
                while inFlight < Self.gitBatch, let index = pending.next() {
                    let path = found[index].path
                    group.addTask { (index, await McpServerRepo.gitState(of: path)) }
                    inFlight += 1
                }
            }
        }
        if mine == ticket { isLoading = false }
    }
}

/// An editor "Open in editor" can hand a folder to.
public struct CodeEditor: Equatable, Sendable, Identifiable {
    public var id: String { name }
    public let name: String
    public let appURL: URL

    public init(name: String, appURL: URL) {
        self.name = name
        self.appURL = appURL
    }
}

/// The editors the app offers, in preference order.
public enum CodeEditors {
    public static let candidates: [(name: String, app: String)] = [
        ("Cursor", "Cursor.app"),
        ("Visual Studio Code", "Visual Studio Code.app"),
        ("Zed", "Zed.app"),
        ("Windsurf", "Windsurf.app"),
        ("Devin", "Devin.app"),
        ("Xcode", "Xcode.app"),
    ]

    /// The candidates present in /Applications or ~/Applications (the
    /// system folder wins), in candidate order.
    public static func installed(
        appExists: (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) }
    ) -> [CodeEditor] {
        let bases = [URL(fileURLWithPath: "/Applications"),
                     FileManager.default.homeDirectoryForCurrentUser.appending(path: "Applications")]
        return candidates.compactMap { name, app in
            bases.lazy.map { $0.appending(path: app) }.first(where: appExists)
                .map { CodeEditor(name: name, appURL: $0) }
        }
    }
}
