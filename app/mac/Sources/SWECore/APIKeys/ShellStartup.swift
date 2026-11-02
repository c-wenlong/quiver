import Foundation

/// The startup file a new terminal's shell reads, and the one line that
/// makes it read the keys file. Nothing runs the shell: detection is a
/// bounded text read that follows the files the rc sources, and the
/// writer appends one marked line.
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

    /// The line's mark, kept on its own so a reader can tell SWE wrote it.
    private static let marker = "# Added by SWE: provider API keys for new terminals"

    /// True when a fresh shell reading this rc text ends up sourcing the
    /// keys file: a non-comment `source` or `.` in command position whose
    /// argument, unquoted and with `~`/`$HOME` expanded, resolves to the
    /// keys file itself. `~/.api_keys` counts only when that path resolves
    /// to the keys file, which is how the legacy symlink after migration
    /// counts and an unrelated file of the same name doesn't.
    ///
    /// Sourcing is transitive: a sourced file is scanned the same way, a
    /// glob argument (`dir/*.zsh`) or `for`-loop variable
    /// (`for f in dir/**/*.zsh; do source "$f"; done`) expands on disk,
    /// so drop-in folders count. Functions, subshells, missing files and
    /// cycles don't. Reads stay bounded: `maxSourcedDepth` hops,
    /// `maxSourcedReads` files, `maxSourcedBytes` each.
    public static func sources(_ rcText: String, keysFile: URL, home: URL) -> Bool {
        let target = keysFile.resolvingSymlinksInPath().standardizedFileURL
        var seen: Set<URL> = []
        var budget = maxSourcedReads
        return sources(rcText, target: target, home: home, depth: 0,
                       seen: &seen, budget: &budget)
    }

    private static let maxSourcedDepth = 4
    private static let maxSourcedReads = 64
    private static let maxSourcedBytes = 512 * 1024
    private static let maxGlobMatches = 256

    private static func sources(_ rcText: String, target: URL, home: URL,
                                depth: Int, seen: inout Set<URL>, budget: inout Int) -> Bool {
        // Braces of a function body still open: a `source` in a function
        // only runs if something calls it, so it doesn't count.
        var functionDepth = 0
        var functionPending = false
        /// Variables a `for NAME in WORDS` loop bound in this file, so a
        /// later `source "$NAME"` expands to the loop's word list.
        var forVars: [String: [String]] = [:]
        /// The word list of a `for` still being collected, if any.
        var collecting: (name: String, words: [String], paren: Bool)?
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
            var parenDepth = 0
            var wanted = false
            var index = 0
            while index < words.count {
                let word = words[index]
                index += 1
                if var c = collecting {
                    // The `in`-list ends at `;`, `do` or `{`, the `( )`
                    // list at `)`; anything else is a word of the list.
                    let closes = c.paren ? word == ")"
                        : word == ";" || word == "do" || word == "{"
                    if closes {
                        forVars[c.name] = c.words
                        collecting = nil
                        atCommand = true
                        wanted = false
                    } else {
                        c.words.append(word)
                        collecting = c
                    }
                    continue
                }
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
                    parenDepth += 1; atCommand = true; wanted = false; continue
                case ")":
                    parenDepth = max(0, parenDepth - 1); atCommand = false; wanted = false; continue
                case "&&", ";", "|":
                    atCommand = true; wanted = false; continue
                default:
                    break
                }
                if wanted {
                    if sourcedWord(word, home: home, forVars: forVars, target: target,
                                   depth: depth, seen: &seen, budget: &budget) {
                        return true
                    }
                    wanted = false
                    atCommand = false
                    continue
                }
                if atCommand {
                    // `for NAME in WORDS` and zsh's `for NAME (WORDS)`.
                    if word == "for", parenDepth == 0, functionDepth == 0,
                       index < words.count, isVariableName(words[index]) {
                        let name = words[index]
                        index += 1
                        if index < words.count, words[index] == "in" {
                            index += 1
                            collecting = (name, [], false)
                        } else if index < words.count, words[index] == "(" {
                            index += 1
                            collecting = (name, [], true)
                        }
                        atCommand = false
                        continue
                    }
                    if (word == "source" || word == "."), parenDepth == 0, functionDepth == 0 { wanted = true }
                    atCommand = ["then", "do", "else", "if", "elif", "while", "until", "!", "{"].contains(word)
                }
            }
            // A `for` list reaching the line's end is complete: the shell
            // ends the word list at the newline.
            if let c = collecting, !c.paren { forVars[c.name] = c.words }
            collecting = nil
        }
        return false
    }

    /// One `source` argument. A `$var` bound by a `for` loop expands to
    /// the loop's words; each word is then a literal file or a glob.
    /// The keys file ends the search; any other real file it names gets
    /// the same scan, within the shared depth and read budget.
    private static func sourcedWord(_ word: String, home: URL,
                                    forVars: [String: [String]], target: URL,
                                    depth: Int, seen: inout Set<URL>, budget: inout Int) -> Bool {
        var args = [word]
        if let name = bareVariable(word), let bound = forVars[name] { args = bound }
        for arg in args {
            guard let path = expandedPath(arg, home: home) else { continue }
            if path.contains(where: isWildcard) {
                for url in expandGlob(path)
                where reaches(url, target: target, home: home,
                              depth: depth, seen: &seen, budget: &budget) {
                    return true
                }
            } else if reaches(URL(fileURLWithPath: path), target: target, home: home,
                              depth: depth, seen: &seen, budget: &budget) {
                return true
            }
        }
        return false
    }

    /// True when `url` is the keys file, or a real file whose own text
    /// sources it. `seen` breaks cycles and repeat reads spend `budget`.
    private static func reaches(_ url: URL, target: URL, home: URL,
                                depth: Int, seen: inout Set<URL>, budget: inout Int) -> Bool {
        let real = url.resolvingSymlinksInPath().standardizedFileURL
        if real == target { return true }
        guard depth < maxSourcedDepth, budget > 0, seen.insert(real).inserted,
              let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]),
              values.isRegularFile == true, (values.fileSize ?? .max) <= maxSourcedBytes,
              let data = try? Data(contentsOf: url),
              let text = String(data: data, encoding: .utf8) else { return false }
        budget -= 1
        return sources(text, target: target, home: home, depth: depth + 1,
                       seen: &seen, budget: &budget)
    }

    /// `$name` or `${name}` as the variable's name, nil for anything else.
    private static func bareVariable(_ word: String) -> String? {
        var text = word
        if text.hasPrefix("${") {
            guard text.hasSuffix("}") else { return nil }
            text = String(text.dropFirst(2).dropLast())
        } else if text.hasPrefix("$") {
            text = String(text.dropFirst())
        } else {
            return nil
        }
        return isVariableName(text) ? text : nil
    }

    private static func isVariableName(_ text: String) -> Bool {
        guard let first = text.first, first.isLetter || first == "_" else { return false }
        return text.allSatisfy { $0.isLetter || $0.isNumber || $0 == "_" }
    }

    private static func isWildcard(_ ch: Character) -> Bool {
        ch == "*" || ch == "?" || ch == "["
    }

    /// The argument of a `source`/`dot` command with its prefix expanded:
    /// a leading `~`, `$HOME` or `${HOME}`, absolute paths as is, anything
    /// relative taken against home. Wildcards stay in the result; the
    /// caller decides whether to glob.
    private static func expandedPath(_ argument: String, home: URL) -> String? {
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
        return text.isEmpty ? nil : text
    }

    /// A `*`/`?`/`[…]` path expanded against the filesystem: `**` matches
    /// folders at any depth, a trailing `(…)` zsh qualifier drops off, and
    /// a wildcard never matches a dot-name unless it starts with one.
    private static func expandGlob(_ pattern: String) -> [URL] {
        var pattern = pattern
        if pattern.hasSuffix(")"), let open = pattern.lastIndex(of: "("),
           open > (pattern.lastIndex(of: "/") ?? pattern.startIndex),
           pattern[..<open].contains(where: isWildcard) {
            pattern = String(pattern[..<open])
        }
        let parts = pattern.split(separator: "/").map(String.init)
        let manager = FileManager.default
        var level = pattern.hasPrefix("/") ? [URL(fileURLWithPath: "/")] : [URL(fileURLWithPath: "")]
        var found: [URL] = []
        for (i, part) in parts.enumerated() {
            var next: [URL] = []
            for dir in level {
                if part == "**" {
                    if i == parts.count - 1 {
                        found.append(contentsOf: descendants(dir, directoriesOnly: false,
                                                             limit: maxGlobMatches - found.count))
                    } else {
                        next.append(contentsOf: descendants(dir, directoriesOnly: true,
                                                            limit: maxGlobMatches - next.count))
                    }
                } else if part.contains(where: isWildcard) {
                    for name in (try? manager.contentsOfDirectory(atPath: dir.path)) ?? []
                    where (!name.hasPrefix(".") || part.hasPrefix(".")) && wildcardMatch(part, name) {
                        next.append(dir.appending(path: name))
                    }
                } else {
                    let child = dir.appending(path: part)
                    if manager.fileExists(atPath: child.path) { next.append(child) }
                }
                if next.count >= maxGlobMatches { break }
            }
            level = Array(next.prefix(maxGlobMatches))
            if level.isEmpty { break }
        }
        found.append(contentsOf: level)
        return found
    }

    /// Every folder under `dir` (`directoriesOnly`), or every entry,
    /// including `dir` itself for the folder listing. Hidden entries are
    /// skipped the way a wildcard skips dot-names.
    private static func descendants(_ dir: URL, directoriesOnly: Bool, limit: Int) -> [URL] {
        guard limit > 0, let enumerator = FileManager.default.enumerator(
            at: dir, includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]) else { return [] }
        var out: [URL] = directoriesOnly ? [dir] : []
        for case let url as URL in enumerator where out.count < limit {
            let isDir = (try? url.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory ?? false
            if isDir || !directoriesOnly { out.append(url) }
        }
        return out
    }

    /// fnmatch for one path component: `*` any run, `?` one character,
    /// `[…]` a class or range, `[!…]`/`[^…]` its complement, an unclosed
    /// `[` a literal.
    private static func wildcardMatch(_ pattern: String, _ name: String) -> Bool {
        let p = Array(pattern), s = Array(name)
        var memo: [Int: Bool] = [:]
        func dp(_ i: Int, _ j: Int) -> Bool {
            if let hit = memo[i * 4096 + j] { return hit }
            let result: Bool
            if i == p.count {
                result = j == s.count
            } else if p[i] == "*" {
                result = dp(i + 1, j) || (j < s.count && dp(i, j + 1))
            } else if j == s.count {
                result = false
            } else if p[i] == "?" {
                result = dp(i + 1, j + 1)
            } else if p[i] == "[", let (matched, next) = classMatch(p, at: i, against: s[j]) {
                result = matched && dp(next, j + 1)
            } else {
                result = p[i] == s[j] && dp(i + 1, j + 1)
            }
            memo[i * 4096 + j] = result
            return result
        }
        return dp(0, 0)
    }

    /// The `[…]` class starting at `i` matched against `ch`: whether it
    /// hit and the index past the closing `]`, nil when unclosed.
    private static func classMatch(_ p: [Character], at i: Int,
                                   against ch: Character) -> (Bool, Int)? {
        var j = i + 1
        var negate = false
        if j < p.count, p[j] == "!" || p[j] == "^" { negate = true; j += 1 }
        var hit = false
        var first = true
        while j < p.count, p[j] != "]" || first {
            first = false
            if j + 2 < p.count, p[j + 1] == "-", p[j + 2] != "]" {
                if p[j] <= ch, ch <= p[j + 2] { hit = true }
                j += 3
            } else {
                if p[j] == ch { hit = true }
                j += 1
            }
        }
        guard j < p.count else { return nil }
        return (hit != negate, j + 1)
    }

    /// Whether an edit to `file` could land at all: the file writable
    /// when it exists, and the folder writable for the temp-and-rename.
    /// A managed rc (a home-manager link into /nix/store, a read-only
    /// checkout) fails here rather than mid-write.
    public static func writable(_ file: URL) -> Bool {
        let manager = FileManager.default
        if manager.fileExists(atPath: file.path), !manager.isWritableFile(atPath: file.path) {
            return false
        }
        var dir = file.deletingLastPathComponent()
        while !manager.fileExists(atPath: dir.path) {
            let parent = dir.deletingLastPathComponent()
            if parent.path == dir.path { return false }
            dir = parent
        }
        return manager.isWritableFile(atPath: dir.path)
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
        /// The resolved rc can't be written, so no edit is attempted.
        case unwritable(URL)

        public var errorDescription: String? {
            switch self {
            case .unsupportedShell(let shell):
                "SWE doesn't know which startup file \(shell) reads. Add the line to it yourself."
            case .unreadable(let file):
                "\(file.path) isn't plain UTF-8 text, so SWE left it alone. Add the line in an editor."
            case .backupFailed(let file):
                "Couldn't write the backup at \(file.path), so SWE didn't change your startup file."
            case .changedOnDisk(let file):
                "\(file.path) changed while SWE was editing it. Try again."
            case .unwritable(let file):
                file.path.hasPrefix("/nix/store/")
                    ? "\(file.path) is a read-only Nix store file. Add the line in the Nix or home-manager config that generates it."
                    : "\(file.path) is read-only, so SWE can't edit it. Add the line in the config that manages it."
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
        // A symlinked rc writes through to the real file, which may be
        // read-only (a Nix store path) or sit in a folder the rename
        // can't touch; fail here rather than at the temp write.
        guard ShellStartup.writable(real) else { throw Error.unwritable(real) }

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
