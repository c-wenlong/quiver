import Foundation

/// The startup file a new terminal's shell reads, and the one line that
/// makes it read the keys file. Nothing runs the shell: detection is a
/// text read, and the writer appends one marked line.
public enum ShellStartup {
    /// `~/.zshrc` for zsh (ZDOTDIR wins when set), `~/.bash_profile` for
    /// bash, nil for anything else.
    public static func startupFile(shell: String, home: URL,
                                   environment: [String: String] = [:]) -> URL? {
        switch URL(fileURLWithPath: shell).lastPathComponent {
        case "zsh":
            if let zdotdir = environment["ZDOTDIR"], !zdotdir.isEmpty {
                return URL(fileURLWithPath: zdotdir).appending(path: ".zshrc")
            }
            return home.appending(path: ".zshrc")
        case "bash":
            return home.appending(path: ".bash_profile")
        default:
            return nil
        }
    }

    /// `[ -f "…" ] && source "…"`, with `$HOME` for a file under home so
    /// the line survives the home folder being renamed.
    public static func sourceLine(for keysFile: URL, home: URL) -> String {
        let path = keysFile.path
        let homePath = home.path
        // Escape the path only: the $HOME alias we add must expand.
        let shown = path.hasPrefix(homePath + "/")
            ? "$HOME/" + escape(String(path.dropFirst(homePath.count + 1)))
            : escape(path)
        return "[ -f \"\(shown)\" ] && source \"\(shown)\""
    }

    /// `"` `\` `$` `` ` `` are the characters a double-quoted shell string
    /// would still expand.
    private static func escape(_ path: String) -> String {
        var out = ""
        for ch in path {
            if "\"\\$`".contains(ch) { out.append("\\") }
            out.append(ch)
        }
        return out
    }

    /// The line's mark, kept on its own so a reader can tell Quiver wrote it.
    private static let marker = "# Added by Quiver: provider API keys for new terminals"

    /// True when the rc text already sources the keys file: a non-comment
    /// line that runs `source` or `.` and mentions the file. The file can
    /// be named by its path, a `~`/`$HOME` form, or just `.api_keys`, since
    /// the legacy `~/.api_keys` is a symlink to the migrated location.
    public static func sources(_ rcText: String, keysFile: URL, home: URL) -> Bool {
        let path = keysFile.path
        var mentions = [path, ".api_keys"]
        for alias in ["~", "$HOME"] where path.hasPrefix(home.path + "/") {
            mentions.append(alias + path.dropFirst(home.path.count))
        }
        for raw in rcText.components(separatedBy: .newlines) {
            var line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            // A trailing comment doesn't count for or against.
            if let hash = line.firstIndex(of: "#") { line = String(line[..<hash]) }
            let isSource = line.contains("source")
                || line.hasPrefix(". ") || line.hasPrefix(".\"") || line.hasPrefix(".'")
            guard isSource else { continue }
            if mentions.contains(where: { line.contains($0) }) { return true }
        }
        return false
    }

    /// The rc text plus the marked line. Empty text gets no leading blank
    /// line; text ending mid-line gets a newline first.
    public static func appending(_ rcText: String, line: String) -> String {
        var out = rcText
        if !out.isEmpty && !out.hasSuffix("\n") { out += "\n" }
        return out + "\(marker)\n\(line)\n"
    }
}

/// Adds the source line to the startup file, backing it up first.
public struct ShellStartupWriter: Sendable {
    public let home: URL
    /// Where the pre-edit copy lands, shared with the keys file backups.
    public let backupDirectory: URL
    /// Stamps backup names; tests pin it.
    public var now: @Sendable () -> Date

    public init(home: URL, backupDirectory: URL, now: @escaping @Sendable () -> Date = Date.init) {
        self.home = home
        self.backupDirectory = backupDirectory
        self.now = now
    }

    public enum Error: Swift.Error, Equatable {
        /// No startup file is known for this shell.
        case unsupportedShell(String)
    }

    /// Appends the source line to the shell's startup file. Already
    /// sourced is a no-op — the rc file and nil backup come back, nothing
    /// is written. Returns the rc path and the backup made (nil when the
    /// file was just created or already had the line). A symlinked rc is
    /// kept a link: the write goes through to the real file.
    @discardableResult
    public func addSource(keysFile: URL, shell: String,
                          environment: [String: String] = [:]) throws -> (file: URL, backup: URL?) {
        guard let rc = ShellStartup.startupFile(shell: shell, home: home, environment: environment) else {
            throw Error.unsupportedShell(shell)
        }
        let real = rc.resolvingSymlinksInPath()
        let manager = FileManager.default
        let existing = (try? String(contentsOf: real, encoding: .utf8)) ?? ""
        let line = ShellStartup.sourceLine(for: keysFile, home: home)
        if ShellStartup.sources(existing, keysFile: keysFile, home: home) {
            return (rc, nil)
        }

        var backup: URL?
        if let data = manager.contents(atPath: real.path) {
            try manager.createDirectory(at: backupDirectory, withIntermediateDirectories: true,
                                        attributes: [.posixPermissions: 0o700])
            try? manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: backupDirectory.path)
            let stamp = Self.stampFormatter.string(from: now())
            backup = backupDirectory.appending(path: "\(rc.lastPathComponent).\(stamp).bak")
            manager.createFile(atPath: backup!.path, contents: data,
                               attributes: [.posixPermissions: 0o600])
        }

        let mode = (try? manager.attributesOfItem(atPath: real.path)[.posixPermissions] as? Int) ?? 0o644
        let text = ShellStartup.appending(existing, line: line)
        let temp = real.deletingLastPathComponent()
            .appending(path: ".\(real.lastPathComponent).quiver-\(UUID().uuidString)")
        guard manager.createFile(atPath: temp.path, contents: Data(text.utf8),
                                 attributes: [.posixPermissions: mode]) else {
            throw CocoaError(.fileWriteUnknown)
        }
        guard rename(temp.path, real.path) == 0 else {
            try? manager.removeItem(at: temp)
            throw CocoaError(.fileWriteUnknown)
        }
        return (rc, backup)
    }

    private static let stampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter
    }()
}
