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
            // Bash reads the first of these that exists; writing to
            // .bash_profile beside an existing .bash_login would shadow
            // it, so pick the first present and default only when none is.
            for file in [".bash_profile", ".bash_login", ".profile"] {
                let candidate = home.appending(path: file)
                if FileManager.default.fileExists(atPath: candidate.path) {
                    return candidate
                }
            }
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
    /// line whose `source` or `.` argument, unquoted and with `~`/`$HOME`
    /// expanded, resolves to the keys file itself. `~/.api_keys` counts
    /// only when that path resolves to the keys file, which is how the
    /// legacy symlink after migration counts and an unrelated file of the
    /// same name doesn't.
    public static func sources(_ rcText: String, keysFile: URL, home: URL) -> Bool {
        let target = keysFile.resolvingSymlinksInPath().standardizedFileURL
        // Braces of a function body still open: a `source` in a function
        // only runs if something calls it, so it doesn't count.
        var functionDepth = 0
        var functionPending = false
        for raw in rcText.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") { continue }
            // Shell words, so a quoted path with spaces, escapes or `#`
            // stays one argument and a trailing comment drops off; `&&`,
            // `;`, `|` and parentheses are words of their own.
            let words = shellWords(line)
            // Only a `source` in command position runs: first on the line,
            // after a separator, or after a keyword like `then`.
            // `echo source X` prints, it doesn't load.
            var atCommand = true
            // A `source` inside `( … )` runs in a subshell, and the
            // terminal keeps nothing it sets.
            var depth = 0
            var wanted = false
            var index = 0
            while index < words.count {
                let word = words[index]
                index += 1
                // `name() {` and `function name {` open a function body.
                if atCommand, word == "function" {
                    functionPending = true; atCommand = false; continue
                }
                if word == "(", index < words.count, words[index] == ")" {
                    index += 1
                    functionPending = true; atCommand = false; wanted = false; continue
                }
                if word == "{", functionPending || functionDepth > 0 {
                    functionDepth += 1; functionPending = false; atCommand = true; continue
                }
                if word == "}", functionDepth > 0 {
                    functionDepth -= 1; atCommand = false; continue
                }
                switch word {
                case "(":
                    depth += 1; atCommand = true; wanted = false; continue
                case ")":
                    depth = max(0, depth - 1); atCommand = false; wanted = false; continue
                case "&&", ";", "|":
                    atCommand = true; wanted = false; continue
                default:
                    break
                }
                if wanted {
                    if let url = sourcedURL(word, home: home),
                       url.resolvingSymlinksInPath().standardizedFileURL == target {
                        return true
                    }
                    wanted = false
                    atCommand = false
                    continue
                }
                if atCommand {
                    if (word == "source" || word == "."), depth == 0, functionDepth == 0 { wanted = true }
                    atCommand = ["then", "do", "else", "if", "elif", "while", "until", "!", "{"].contains(word)
                }
            }
        }
        return false
    }

    /// A line split the way a shell reads it: quotes group and come off,
    /// `\\` escapes outside single quotes, and `&&` `;` `|` `(` `)` are
    /// separate words.
    /// Stands in for a `\\$` so `sourcedURL` never expands it: the shell
    /// reads an escaped dollar literally.
    static let escapedDollar: Character = "\u{1}"

    static func shellWords(_ line: String) -> [String] {
        var words: [String] = []
        var word = ""
        var open = false
        var quote: Character?
        let chars = Array(line)
        var i = 0
        func flush() { if open { words.append(word) }; word = ""; open = false }
        while i < chars.count {
            let ch = chars[i]
            if quote == "'" {
                // Single quotes expand nothing, `$` included.
                if ch == "'" { quote = nil } else { word.append(ch == "$" ? escapedDollar : ch) }
            } else if quote == "\"" {
                if ch == "\"" {
                    quote = nil
                } else if ch == "\\", i + 1 < chars.count, "\"\\$`".contains(chars[i + 1]) {
                    i += 1
                    word.append(chars[i] == "$" ? escapedDollar : chars[i])
                } else {
                    word.append(ch)
                }
            } else if ch == "'" || ch == "\"" {
                quote = ch
                open = true
            } else if ch == "\\", i + 1 < chars.count {
                i += 1
                word.append(chars[i] == "$" ? escapedDollar : chars[i])
                open = true
            } else if ch == "#", !open {
                // An unquoted `#` starting a word begins a comment.
                break
            } else if ch == " " || ch == "\t" {
                flush()
            } else if ch == "&", i + 1 < chars.count, chars[i + 1] == "&" {
                flush()
                words.append("&&")
                i += 1
            } else if ";|()".contains(ch) {
                flush()
                words.append(String(ch))
            } else {
                word.append(ch)
                open = true
            }
            i += 1
        }
        flush()
        return words
    }

    /// The argument of a `source`/`dot` command as a file URL: a leading
    /// `~`, `$HOME` or `${HOME}` expanded, absolute paths as is, anything
    /// relative taken against home.
    private static func sourcedURL(_ argument: String, home: URL) -> URL? {
        var text = argument
        if text.hasPrefix("~/") {
            text = home.path + text.dropFirst(1)
        } else if text == "~" {
            text = home.path
        } else if text.hasPrefix("$HOME/") {
            text = home.path + text.dropFirst(5)
        } else if text.hasPrefix("${HOME}/") {
            text = home.path + text.dropFirst(7)
        } else if text == "$HOME" || text == "${HOME}" {
            text = home.path
        } else if !text.hasPrefix("/") {
            text = home.path + "/" + text
        }
        text = text.replacingOccurrences(of: String(escapedDollar), with: "$")
        guard !text.isEmpty, !text.contains("*") else { return nil }
        return URL(fileURLWithPath: text)
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

    public enum Error: Swift.Error, Equatable, LocalizedError {
        /// No startup file is known for this shell.
        case unsupportedShell(String)
        /// The file exists but isn't UTF-8, so appending would destroy it.
        case unreadable(URL)
        /// The backup could not be written; the rc is left alone.
        case backupFailed(URL)
        /// The rc changed between the read and the write; try again.
        case changedOnDisk(URL)

        public var errorDescription: String? {
            switch self {
            case .unsupportedShell(let shell):
                "Quiver doesn't know which startup file \(shell) reads. Add the line to it yourself."
            case .unreadable(let file):
                "\(file.path) isn't plain UTF-8 text, so Quiver left it alone. Add the line in an editor."
            case .backupFailed(let file):
                "Couldn't write the backup at \(file.path), so Quiver didn't change your startup file."
            case .changedOnDisk(let file):
                "\(file.path) changed while Quiver was editing it. Try again."
            }
        }
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
        var existing = ""
        var oldData: Data?
        do {
            oldData = try Data(contentsOf: real)
        } catch let error as CocoaError where error.code == .fileReadNoSuchFile {
            oldData = nil
        } catch let error as NSError where error.domain == NSPOSIXErrorDomain
                                        && error.code == ENOENT {
            oldData = nil
        } catch {
            // Readable-or-missing was the old assumption; an unreadable
            // existing file must never read as empty and be replaced.
            throw Error.unreadable(real)
        }
        if let data = oldData {
            // Appending decoded text would replace unreadable bytes with
            // just our line; refuse instead.
            guard let text = String(data: data, encoding: .utf8) else {
                throw Error.unreadable(real)
            }
            existing = text
        }
        let line = ShellStartup.sourceLine(for: keysFile, home: home)
        if ShellStartup.sources(existing, keysFile: keysFile, home: home) {
            return (rc, nil)
        }

        var backup: URL?
        if let data = oldData {
            try manager.createDirectory(at: backupDirectory, withIntermediateDirectories: true,
                                        attributes: [.posixPermissions: 0o700])
            try? manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: backupDirectory.path)
            let stamp = Self.stampFormatter.string(from: now())
            let target = backupDirectory.appending(path: "\(rc.lastPathComponent).\(stamp).bak")
            guard manager.createFile(atPath: target.path, contents: data,
                                     attributes: [.posixPermissions: 0o600]) else {
                throw Error.backupFailed(target)
            }
            backup = target
        }

        // A ZDOTDIR folder may not exist yet; the temp file needs it.
        try manager.createDirectory(at: real.deletingLastPathComponent(),
                                    withIntermediateDirectories: true)
        let mode = (try? manager.attributesOfItem(atPath: real.path)[.posixPermissions] as? Int) ?? 0o644
        let text = ShellStartup.appending(existing, line: line)
        let temp = real.deletingLastPathComponent()
            .appending(path: ".\(real.lastPathComponent).swe-\(UUID().uuidString)")
        guard manager.createFile(atPath: temp.path, contents: Data(text.utf8),
                                 attributes: [.posixPermissions: mode]) else {
            throw CocoaError(.fileWriteUnknown)
        }
        beforeReplace()
        // A concurrent edit between the read and now would be lost: check
        // the bytes one last time and abort rather than clobber them.
        if manager.contents(atPath: real.path) != oldData {
            try? manager.removeItem(at: temp)
            throw Error.changedOnDisk(real)
        }
        guard rename(temp.path, real.path) == 0 else {
            try? manager.removeItem(at: temp)
            throw CocoaError(.fileWriteUnknown)
        }
        return (rc, backup)
    }

    /// Runs between the temp write and the rename; tests edit the rc here
    /// to prove the concurrent-edit guard.
    public var beforeReplace: @Sendable () -> Void = {}

    private static let stampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = .current
        return formatter
    }()
}
